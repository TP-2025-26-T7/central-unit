import asyncio
import json
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = float(os.getenv("OMNET_CONNECT_TIMEOUT", "3"))
WRITE_TIMEOUT   = float(os.getenv("OMNET_WRITE_TIMEOUT", "30"))
READ_TIMEOUT    = float(os.getenv("OMNET_READ_TIMEOUT", "120"))

class OmnetClient:
    def __init__(self, host: str = None, port: int = None):
        self.host = host or os.getenv("OMNET_HOST", "192.168.20.51")
        self.port = port or int(os.getenv("OMNET_PORT", 9999))
        self.reader = None
        self.writer = None
        self._lock = asyncio.Lock()
        self.is_connected = False
        self.last_connect_attempt = 0  # Timestamp of last attempt

    async def connect(self):
        """Establishes an async TCP connection to the OMNeT++ bridge."""
        # Update timestamp to prevent rapid retries if checked externally
        self.last_connect_attempt = asyncio.get_event_loop().time()
        
        if self.is_connected:
            return

        print(f"DEBUG: Attempting to connect to OMNeT++ (TCP) at {self.host}:{self.port}...", flush=True)
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=CONNECT_TIMEOUT
            )
            self.is_connected = True
            print("DEBUG: Successfully connected to OMNeT++ bridge (TCP).", flush=True)
        except asyncio.TimeoutError:
            print(f"DEBUG: Connection to OMNeT++ timed out after {CONNECT_TIMEOUT}s - running in passthrough mode", flush=True)
            await self.close()
        except Exception as e:
            print(f"DEBUG: Failed to connect to OMNeT++: {e} - running in passthrough mode", flush=True)
            await self.close()

    async def ensure_connection(self, retries=3):
        """Attempts to connect if not already connected, with retries.
        
        It also proactively checks if the existing connection is healthy.
        """
        print(f"DEBUG: ensure_connection called. Current status: {'Connected' if self.is_connected else 'Disconnected'}", flush=True)
        async with self._lock:
            # Check if socket is actually healthy
            if self.is_connected:
                # If writer is closed, force close and reconnect
                if self.writer is None or self.writer.is_closing():
                    print("DEBUG: Connection marked as active but writer is closed. Forcing cleanup.", flush=True)
                    await self.close()
                else:
                    # Connection seems fine
                    print("DEBUG: Already connected. doing nothing.", flush=True)
                    return

            print(f"DEBUG: Not connected. Attempting to connect with {retries} retries...", flush=True)
            for i in range(retries):
                # Ensure clean slate before connect
                if self.writer is not None:
                     await self.close()

                await self.connect()
                if self.is_connected:
                    print(f"DEBUG: Connected successfully on attempt {i+1}", flush=True)
                    return
                
                print(f"DEBUG: Connection attempt {i+1} failed.", flush=True)
                if i < retries - 1:
                    await asyncio.sleep(1) # Wait a bit between retries
            
            print("DEBUG: All connection attempts failed. Proceeding in passthrough mode.", flush=True)

    async def send_and_receive(self, data: dict) -> dict:
        """Sends data and waits for the corresponding response using TCP."""
        # Check connection status BEFORE locking
        # If not connected, return immediately (passthrough) without waiting for lock
        if not self.is_connected:
            print("DEBUG: in send_and_receive, is_connected=False (fast check) -> PASSTHROUGH", flush=True)
            return data

        # If connected, acquire lock to send safely
        async with self._lock:
            # Re-check inside lock for race conditions
            if not self.is_connected:
                print("DEBUG: in send_and_receive, is_connected=False (locked check) -> PASSTHROUGH", flush=True)
                return data
            
            if self.writer is None or self.writer.is_closing():
                print("DEBUG: Socket writer is None or closing. Attempting to reconnect...", flush=True)
                await self.connect()
                if self.writer is None or not self.is_connected:
                    print("DEBUG: Reconnection failed inside send_and_receive - using passthrough mode", flush=True)
                    return data

            try:
                # Send data with newline delimiter for framing
                message = json.dumps(data).encode('utf-8') + b'\n'
                self.writer.write(message)
                
                # Add timeout for write
                await asyncio.wait_for(self.writer.drain(), timeout=WRITE_TIMEOUT)

                # Read response with timeout
                response_line = await asyncio.wait_for(self.reader.readline(), timeout=READ_TIMEOUT)
                
                if not response_line:
                    await self.close()
                    logger.debug("Connection closed by peer - using passthrough mode")
                    return data

                return json.loads(response_line.decode('utf-8'))

            except asyncio.TimeoutError:
                logger.warning("Socket operation timed out - using passthrough mode")
                await self.close()
                return data
            except Exception as e:
                logger.warning(f"Socket communication error: {e} - using passthrough mode")
                await self.close()
                return data

    async def close(self):
        """Closes the socket connection."""
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except:
                pass
        self.writer = None
        self.reader = None
        self.is_connected = False
        logger.info("OMNeT++ connection closed.")

# Singleton instance
omnet_client = OmnetClient()