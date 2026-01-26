# Central Unit

The Central Unit is a FastAPI-based orchestration service designed to handle communication between traffic simulation modules (like SUMO), algorithmic decision engines, and network simulation bridges (OMNeT++). It acts as the central coordinator, processing telemetry from vehicles and infrastructure to issue instructions.

## Features

*   **FastAPI Framework**: High-performance, asynchronous REST API.
*   **SUMO Integration**: Dedicated endpoints to receive simulation steps and forward them for processing.
*   **SUMO Proxy**: Proxies management and configuration requests to the underlying SUMO API service.
*   **Algorithm Delegation**: Offloads complex decision-making logic to an external Algorithm Runner service.
*   **Persistent OMNeT++ Bridge**: A robust, full-duplex TCP client that maintains a persistent connection to an external OMNeT++ bridge for network simulation data exchange.
*   **Step Logging**: Automatically logs simulation steps and decisions to JSONL files for replay and analysis.
*   **Dockerized**: Ready for deployment using Docker and Docker Compose.

## Getting Started (Linux)

### Prerequisites

*   [Docker](https://docs.docker.com/engine/install/)
*   [Docker Compose](https://docs.docker.com/compose/install/)
*   Git

### Installation & Running

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/TP-2025-26-T7/central-unit.git
    cd central-unit
    ```

2.  **Start the service:**
    Use Docker Compose to build and start the container.
    ```bash
    docker-compose up --build -d
    ```

3.  **Verify installation:**
    Check the logs to ensure the service started and attempted to connect to the OMNeT++ bridge.
    ```bash
    docker-compose logs -f
    ```
    The API will be available at `http://localhost:8001`.
    You can access the interactive API docs at `http://localhost:8001/docs`.

### Configuration

The service can be configured using environment variables (see `docker-compose.yaml`):

| Variable | Description | Default |
| :--- | :--- | :--- |
| `ALG_RUNNER_URL` | URL of the Algorithm Runner service | `http://alg-runner:8000` |
| `SUMO_API_URL` | URL of the SUMO Management API | `http://sumo-api:8002` |
| `PYTHONUNBUFFERED` | Ensure Python output is sent straight to terminal | `1` |

## Architecture & Communication

### OMNeT++ Socket Client

The application features a specialized TCP client ([omnet_socket.py](http://_vscodecontentref_/0)) designed for high-throughput, low-latency communication with an OMNeT++ simulation bridge.

**How it works:**

1.  **Persistent Connection**: On application startup (`lifespan` in `main.py`), the client establishes a TCP connection to the configured host and port (default: `127.0.0.1:9999`). It keeps this connection open for the lifetime of the application.
2.  **Full-Duplex Communication**:
    *   The client runs a background `_read_loop` task that continuously listens for incoming data.
    *   Sending data (`send_and_receive`) is non-blocking and thread-safe.
    *   **Framing**: Messages are sent as JSON strings terminated by a newline character (`\n`).
3.  **Request-Response Matching**:
    *   Requests are handled using a FIFO (First-In-First-Out) queue.
    *   When `send_and_receive` is called, a `Future` is created and added to the queue.
    *   The background reader parses incoming messages and resolves the oldest pending `Future`.
    *   This allows multiple concurrent API calls (e.g., from different cars) to send data to OMNeT++ without blocking the main thread or mixing up responses.

**Usage Example (Internal):**

```python
from app.helpers.omnet_socket import omnet_client

# Inside an async endpoint
response = await omnet_client.send_and_receive({
    "type": "telemetry",
    "car_id": "vehicle_01",
    "speed": 15.5
})
```

### SUMO API Integration

The `app/api/sumo_api.py` module handles the interaction with the SUMO traffic simulation.

**Key Components:**

*   **`POST /sumo/step`**: This is the primary endpoint called by the SUMO simulation loop.
    *   **Workflow**:
        1.  Receives the current simulation state (`SumoStepRequest`) containing `Car` positions/speeds and `Junction` details.
        2.  Forwards this data to the **Algorithm Runner** service (`ALG_RUNNER_URL`) for decision making.
        3.  Logs the step input and the resulting instructions to `data/step_logs/{module_id}.jsonl` for future analysis or visualization.
        4.  Returns the generated list of `Instruction` objects to SUMO.
*   **Data Models**: Pydantic models define the strict typing for `Car`, `Junction`, and `Instruction`.

### Algorithm Runner Integration

The Central Unit acts as a middleware, delegating the complex algorithmic logic to an external service.

*   **Endpoint**: `POST /dispatch` (on the runner service).
*   **Configuration**: Configured via the `ALG_RUNNER_URL` environment variable (default: `http://localhost:8000`).
*   **Function**: Isolate the simulation coordination from the specific traffic control algorithms (e.g., FIFO, optimization models).

### SUMO Proxy

To simplify client interactions, the Central Unit includes a proxy module (`app/api/sumo_proxy.py`) that forwards management and configuration requests to the underlying SUMO API service.

*   **Prefix**: `/api/v1`
*   **Target**: `SUMO_API_URL` (default: `http://sumo-api:8002`).
*   **Supported Endpoints**:
    *   Configuration management (`/config`, `/config/{id}/net`)
    *   Simulation control (`/simulations`, `/simulations/{id}/statistics`)
*   **Behavior**: Transparently proxies headers, query parameters, and body content, while stripping host-specific headers.
