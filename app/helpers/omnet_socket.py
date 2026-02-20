import asyncio
import json
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = float(os.getenv("OMNET_CONNECT_TIMEOUT", "15"))
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

    async def connect(self):
        """Establishes an async TCP connection to the OMNeT++ bridge."""
        logger.info(f"Attempting to connect to OMNeT++ (TCP) at {self.host}:{self.port}...")
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=CONNECT_TIMEOUT
            )
            self.is_connected = True
            logger.info("Successfully connected to OMNeT++ bridge (TCP).")
        except asyncio.TimeoutError:
            logger.warning(f"Connection to OMNeT++ timed out after {CONNECT_TIMEOUT}s - running in passthrough mode")
            self.reader = None
            self.writer = None
            self.is_connected = False
        except Exception as e:
            logger.warning(f"Failed to connect to OMNeT++: {e} - running in passthrough mode")
            self.reader = None
            self.writer = None
            self.is_connected = False

    async def send_and_receive(self, data: dict) -> dict:
        """Sends data and waits for the corresponding response using TCP.
        
        If OMNeT++ is not connected, returns data as-is (passthrough mode).
        """
        async with self._lock:
            # Passthrough mode: if not connected, just return the data
            if not self.is_connected:
                logger.debug("OMNeT++ not connected - using passthrough mode")
                return data
            
            if self.writer is None or self.writer.is_closing():
                logger.warning("Socket is not connected. Attempting to reconnect...")
                await self.connect()
                if self.writer is None or not self.is_connected:
                    logger.debug("Reconnection failed - using passthrough mode")
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