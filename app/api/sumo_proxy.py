import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter(prefix="/api/v1", tags=["sumo-proxy"])

SUMO_API_URL = os.getenv("SUMO_API_URL", "http://sumo-api:8002").rstrip("/")


async def _proxy(request: Request, upstream_path: str) -> Response:
    """
    Generic proxy helper:
    - forwards method, query params, headers (mostly), and body to SUMO API
    - returns upstream status + content + content-type
    """
    url = f"{SUMO_API_URL}{upstream_path}"

    params = dict(request.query_params)

    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    body = await request.body()

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.request(
            method=request.method,
            url=url,
            params=params,
            content=body if body else None,
            headers=headers,
        )

    content_type = r.headers.get("content-type", "application/octet-stream")

    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=content_type,
    )


# ---- Config endpoints ----

@router.get("/config")
async def list_configs(request: Request):
    return await _proxy(request, "/api/v1/config")


@router.post("/config")
async def upload_config(request: Request):
    return await _proxy(request, "/api/v1/config")


@router.delete("/config/{config_id}")
async def delete_config(config_id: str, request: Request):
    return await _proxy(request, f"/api/v1/config/{config_id}")


@router.get("/config/{config_id}/net")
async def get_net(config_id: str, request: Request):
    return await _proxy(request, f"/api/v1/config/{config_id}/net")


# ---- Simulation endpoints ----

@router.post("/simulations")
async def create_simulation(request: Request):
    return await _proxy(request, "/api/v1/simulations")


@router.get("/simulations")
async def list_simulations(request: Request):
    return await _proxy(request, "/api/v1/simulations")


@router.get("/simulations/{sim_id}")
async def get_simulation(sim_id: str, request: Request):
    return await _proxy(request, f"/api/v1/simulations/{sim_id}")


@router.get("/simulations/{sim_id}/statistics")
async def get_statistics(sim_id: str, request: Request):
    return await _proxy(request, f"/api/v1/simulations/{sim_id}/statistics")
