from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(
    prefix="/sumo",
    tags=["sumo"],
)

class Junction(BaseModel):
    junction_id: str
    edge_count: int
    edges: List[str]
    edges_shape: Optional[List[str]] = None


class Car(BaseModel):
    car_id: str
    x: float
    y: float
    speed: float
    acceleration: float


class SumoStepRequest(BaseModel):
    module_id: str
    junctions: List[Junction]
    cars: List[Car]


class Instruction(BaseModel):
    car_id: str
    speed: Optional[float] = None
    # acceleration: Optional[float] = None
    # direction: Optional[float] = None
    # others...


class SumoStepResponse(BaseModel):
    output: List[Instruction]



@router.get("/health")
async def health_check():
    return {"status": "central-unit ok"}


@router.post("/step", response_model=SumoStepResponse)
async def sumo_step(body: SumoStepRequest):

    instructions: List[Instruction] = []

    for car in body.cars:
        instructions.append(
            Instruction(
                car_id=car.car_id,
                speed=10.0,  # <- force speed 10 for all cars
            )
        )

    return SumoStepResponse(output=instructions)
