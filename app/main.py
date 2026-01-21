from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.sumo_api import router as sumo_router
# IMPORTANT: Please rename 'omnet-socket.py' to 'omnet_socket.py' for this import to work
from app.helpers.omnet_socket import omnet_client
from app.api.sumo_proxy import router as sumo_proxy_router
from fastapi.middleware.cors import CORSMiddleware


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
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    
    

    # Register routers here
    app.include_router(sumo_router)
    app.include_router(sumo_proxy_router)

    return app


app = create_app()
