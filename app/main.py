import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.sumo_api import router as sumo_router
# IMPORTANT: Please rename 'omnet-socket.py' to 'omnet_socket.py' for this import to work
from app.helpers.omnet_socket import omnet_client
from app.api.sumo_proxy import router as sumo_proxy_router
from fastapi.middleware.cors import CORSMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Connect to OMNeT++ (non-blocking)
    try:
        await omnet_client.connect()
    except Exception as e:
        print(f"Warning: OMNeT++ connection failed: {e}. App will run without OMNeT support.")
    
    # Start background task to maintain connection
    async def maintain_connection():
        while True:
            try:
                if not omnet_client.is_connected:
                    print("DEBUG: Background task checking OMNeT++ connection...", flush=True)
                    await omnet_client.ensure_connection(retries=1)
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"ERROR: maintain_connection task failed: {e}", flush=True)
                await asyncio.sleep(10)

    task = asyncio.create_task(maintain_connection())
    
    yield
    
    # Shutdown: Close connection and cancel background task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
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
    app.include_router(sumo_proxy_router, prefix="/sumo")
    return app


app = create_app()
