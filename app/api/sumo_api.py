import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, ConfigDict, Field
from app.helpers.omnet_socket import omnet_client

router = APIRouter(
    prefix="/sumo",
    tags=["sumo"],
)

ALG_RUNNER_URL = os.getenv("ALG_RUNNER_URL", "http://localhost:8000")
STEP_LOG_DIR = Path("data/step_logs")
STEP_LOG_DIR.mkdir(parents=True, exist_ok=True)

seen_modules = set()


class Junction(BaseModel):
    junction_id: str
    edge_count: int
    edges: List[str]
    edges_shape: Optional[List[str]] = None


class Car(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    car_id: str
    x: float
    y: float
    speed: float
    acceleration: float
    next_junction_id: Optional[str] = None
    next_junction_x: Optional[float] = None
    next_junction_y: Optional[float] = None
    lane_id: Optional[str] = Field(default=None, alias="lane")
    road_id: Optional[str] = Field(default=None, alias="road")


class SumoStepRequest(BaseModel):
    module_id: str
    junctions: List[Junction]
    cars: List[Car]
    algorithm_name: str | None = "fifo"


class Instruction(BaseModel):
    car_id: str
    speed: Optional[float] = None
    acceleration: Optional[float] = None


class SumoStepResponse(BaseModel):
    output: List[Instruction]


def _append_step_log(module_id: str, payload: dict, instructions: list[dict], duration_s: float) -> None:
    """Persist each step for later playback on the frontend."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "module_id": module_id,
        "duration_s": round(duration_s, 4),
        "payload": payload,
        "instructions": instructions,
    }

    log_path = STEP_LOG_DIR / f"{module_id}.jsonl"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


@router.get("/health")
async def health_check():
    return {"status": "central-unit ok"}


@router.get("/step-log/{module_id}")
async def fetch_step_log(module_id: str):
    """Return the logged steps for a module."""
    log_path = STEP_LOG_DIR / f"{module_id}.jsonl"
    if not log_path.exists():
        return {"module_id": module_id, "steps": []}

    steps = []
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                steps.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return {"module_id": module_id, "steps": steps}


@router.post("/step", response_model=SumoStepResponse)
async def sumo_step(body: SumoStepRequest, background_tasks: BackgroundTasks):
    junction_payloads = []
    if body.module_id not in seen_modules:
        for junction in body.junctions:
            data = junction.model_dump(mode="json")
            data.setdefault("connected_roads_ids", data.get("edges", []))
            data.setdefault("connected_roads_count", data.get("edge_count", 0))
            junction_payloads.append(data)
        seen_modules.add(body.module_id)

    payload = {
        "algorithm_name": body.algorithm_name or "fifo",
        "cars": [car.model_dump(mode="json") for car in body.cars],
        "junctions": junction_payloads,
    }

    # Bounce off OMNeT++ (Outbound: Sumo -> CU -> OMNET -> CU -> Alg)
    # If OMNeT++ is not available, this returns the payload unchanged (passthrough mode)
    alg_payload = await omnet_client.send_and_receive(payload)

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{ALG_RUNNER_URL}/dispatch", json=alg_payload)
        response.raise_for_status()
        cars = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"alg-runner timeout: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        error_detail = f"alg-runner returned {exc.response.status_code}: {exc.response.text}"
        print(f"ERROR: {error_detail}")
        raise HTTPException(status_code=502, detail=error_detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"alg-runner error: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}") from exc
    duration = time.perf_counter() - start

    # Bounce off OMNeT++ (Inbound: Alg -> CU -> OMNET -> CU -> Sumo)
    # If OMNeT++ is not available, this returns the payload unchanged (passthrough mode)
    omnet_back_payload = {"cars": cars}
    cars_response = await omnet_client.send_and_receive(omnet_back_payload)

    # Extract cars from OMNeT++ response (or passthrough data)
    if "cars" in cars_response:
        cars = cars_response["cars"]

    instructions: List[Instruction] = []
    for car in cars:
        car_id = car.get("car_id")
        if not car_id:
            continue
        instructions.append(
            Instruction(
                car_id=car_id,
                speed=car.get("speed"),
                acceleration=car.get("acceleration"),
            )
        )

    background_tasks.add_task(
        _append_step_log,
        body.module_id,
        body.model_dump(mode="json"),
        [inst.model_dump(mode="json") for inst in instructions],
        duration,
    )

    return SumoStepResponse(output=instructions)
