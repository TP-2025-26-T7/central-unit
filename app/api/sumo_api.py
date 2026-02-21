import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict

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

# Storage for registered junctions per module
registered_junctions: Dict[str, List[dict]] = {}
# Track pending cars per step (module_id -> {step_id -> list of cars})
pending_cars: Dict[str, Dict[int, List[dict]]] = {}


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
    junctions: List[Junction] = []  # Now optional, can be empty
    cars: List[Car] = []  # Now optional, can be empty
    algorithm_name: str | None = "fifo"


# New request models for separate endpoints
class RegisterJunctionsRequest(BaseModel):
    module_id: str
    junctions: List[Junction]
    algorithm_name: str | None = "fifo"


class SingleCarRequest(BaseModel):
    module_id: str
    step_id: int
    car: Car
    algorithm_name: str | None = "fifo"
    total_cars: int  # Total cars expected in this step


class StepCompleteRequest(BaseModel):
    module_id: str
    step_id: int
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


# ---- New endpoints for realistic V2I communication ----

@router.post("/register-junctions")
async def register_junctions(body: RegisterJunctionsRequest):
    """
    Register junctions for a module. Called once at simulation start.
    This simulates the infrastructure being known to the central unit.
    """
    print(f"DEBUG: register_junctions called for module {body.module_id}", flush=True)
    
    # Try multiple connect attempts (e.g. 5) at start of simulation
    # If this fails, the client stays in disconnected state and uses passthrough.
    await omnet_client.ensure_connection(retries=5)

    junction_payloads = []
    for junction in body.junctions:
        data = junction.model_dump(mode="json")
        data.setdefault("connected_roads_ids", data.get("edges", []))
        data.setdefault("connected_roads_count", data.get("edge_count", 0))
        junction_payloads.append(data)
    
    registered_junctions[body.module_id] = junction_payloads
    # Initialize pending cars storage for this module
    pending_cars[body.module_id] = {}
    
    return {
        "status": "registered",
        "module_id": body.module_id,
        "junction_count": len(junction_payloads)
    }


@router.post("/car")
async def receive_single_car(body: SingleCarRequest):
    """
    Receive a single car's data. Cars are forwarded to OMNeT++ immediately,
    and the result is collected until step-complete is called.
    """
    module_id = body.module_id
    step_id = body.step_id
    
    # Ensure module has pending cars storage
    if module_id not in pending_cars:
        pending_cars[module_id] = {}
    
    # Initialize step storage if needed
    if step_id not in pending_cars[module_id]:
        pending_cars[module_id][step_id] = []
    
    # Prepare payload for OMNeT++ (per car)
    car_data = body.car.model_dump(mode="json")
    junction_payloads = registered_junctions.get(module_id, [])
    
    payload = {
        "algorithm_name": body.algorithm_name or "fifo",
        "cars": [car_data],
        "junctions": junction_payloads,
    }
    
    # Forward to OMNeT++ immediately
    omnet_response = await omnet_client.send_and_receive(payload)
    
    # Extract processed cars (if any) and store them
    processed_cars = omnet_response.get("cars", [car_data])  # Fallback to original if key missing
    
    pending_cars[module_id][step_id].extend(processed_cars)
    
    cars_received = len(pending_cars[module_id][step_id])
    
    return {
        "status": "received",
        "car_id": body.car.car_id,
        "step_id": step_id,
        "cars_received": cars_received,
        "total_expected": body.total_cars
    }


@router.post("/step-complete", response_model=SumoStepResponse)
async def step_complete(body: StepCompleteRequest, background_tasks: BackgroundTasks):
    """
    Signal that all cars for this step have been sent.
    Triggers the algorithm computation and returns instructions for all cars.
    """
    # Periodic reconnection attempt (every 10s) if currently disconnected
    # This handles restarts or recovery from passthrough mode
    if not omnet_client.is_connected:
        now = asyncio.get_event_loop().time()
        if now - omnet_client.last_connect_attempt > 10.0:
            print("DEBUG: Periodic reconnection attempt in step_complete...", flush=True)
            await omnet_client.ensure_connection(retries=1)

    module_id = body.module_id
    step_id = body.step_id
    
    # Get registered junctions (empty if not registered)
    junction_payloads = registered_junctions.get(module_id, [])
    
    # Get pending cars for this step
    cars = []
    if module_id in pending_cars and step_id in pending_cars[module_id]:
        cars = pending_cars[module_id][step_id]
        # Clean up processed step
        del pending_cars[module_id][step_id]
    
    # Payload for Alg Runner (cars are already processed by OMNeT++)
    alg_payload = {
        "algorithm_name": body.algorithm_name or "fifo",
        "cars": cars,
        "junctions": junction_payloads,  # Send junctions every time to alg-runner
    }

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{ALG_RUNNER_URL}/dispatch", json=alg_payload)
        response.raise_for_status()
        result_cars = response.json()
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

    # Bounce off OMNeT++ (Inbound)
    omnet_back_payload = {"cars": result_cars}
    cars_response = await omnet_client.send_and_receive(omnet_back_payload)

    if "cars" in cars_response:
        result_cars = cars_response["cars"]

    instructions: List[Instruction] = []
    for car in result_cars:
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

    log_payload = {
        "module_id": module_id,
        "step_id": step_id,
        "cars": cars,
        "algorithm_name": body.algorithm_name,
    }
    background_tasks.add_task(
        _append_step_log,
        module_id,
        log_payload,
        [inst.model_dump(mode="json") for inst in instructions],
        duration,
    )

    return SumoStepResponse(output=instructions)


# ---- Legacy endpoint (kept for backward compatibility) ----


@router.post("/step", response_model=SumoStepResponse)
async def sumo_step(body: SumoStepRequest, background_tasks: BackgroundTasks):
    """
    Legacy endpoint: receives all cars and junctions in one request.
    Junctions are only processed on the first call per module_id.
    """
    # Defensive logic: if we are in passthrough mode, try to recover periodically
    # E.g. every 10 seconds, make ONE attempt to reconnect.
    # This ensures "simulations started later" get a chance to connect.
    if not omnet_client.is_connected:
        now = asyncio.get_event_loop().time()
        # Retry every 10 seconds if disconnected
        if now - omnet_client.last_connect_attempt > 10.0:
            print("DEBUG: Periodic reconnection attempt in sumo_step...", flush=True)
            # Try once, don't block for long
            await omnet_client.ensure_connection(retries=1)

    junction_payloads = []
    # Only process junctions if not already registered for this module
    if body.module_id not in registered_junctions:
        print(f"DEBUG: sumo_step (first call for {body.module_id}) - attempting to connect...", flush=True)
        # Force a stronger retry on new module registration
        await omnet_client.ensure_connection(retries=5)
        
        for junction in body.junctions:
            data = junction.model_dump(mode="json")
            data.setdefault("connected_roads_ids", data.get("edges", []))
            data.setdefault("connected_roads_count", data.get("edge_count", 0))
            junction_payloads.append(data)
        registered_junctions[body.module_id] = junction_payloads
    else:
        # Use already registered junctions
        junction_payloads = registered_junctions[body.module_id]

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
