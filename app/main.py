from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.sumo_api import router as sumo_router
# IMPORTANT: Please rename 'omnet-socket.py' to 'omnet_socket.py' for this import to work
from app.helpers.omnet_socket import omnet_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Connect to OMNeT++
    await omnet_client.connect()
    yield
    # Shutdown: Close connection
    await omnet_client.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Central Unit",
        version="1.0.0",
        lifespan=lifespan
    )

    # Register routers here
    app.include_router(sumo_router)

    return app


app = create_app()
