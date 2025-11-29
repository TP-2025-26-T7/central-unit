from fastapi import FastAPI
from app.api.sumo_api import router as sumo_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Central Unit",
        version="1.0.0",
    )

    # Register routers here
    app.include_router(sumo_router)

    return app


app = create_app()
