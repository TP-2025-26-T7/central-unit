import asyncio
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OmnetClient:
    def __init__(self, host: str = "192.168.20.51", port: int = 9999):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self._lock = asyncio.Lock()

    async def connect(self):
        """Establishes an async TCP connection to the OMNeT++ bridge."""
        logger.info(f"Attempting to connect to OMNeT++ (TCP) at {self.host}:{self.port}...")
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            logger.info("Successfully connected to OMNeT++ bridge (TCP).")
        except Exception as e:
            logger.error(f"Failed to connect to OMNeT++: {e}")
            self.reader = None
            self.writer = None

    async def send_and_receive(self, data: dict) -> dict:
        """Sends data and waits for the corresponding response using TCP."""
        async with self._lock:
            if self.writer is None or self.writer.is_closing():
                logger.warning("Socket is not connected. Attempting to reconnect...")
                await self.connect()
                if self.writer is None:
                    return {"error": "Connection unavailable"}

            try:
                # Send data with newline delimiter for framing
                message = json.dumps(data).encode('utf-8') + b'\n'
                self.writer.write(message)
                await self.writer.drain()

                # Read response
                response_line = await self.reader.readline()
                if not response_line:
                    return {"error": "Connection closed by peer"}

                return json.loads(response_line.decode('utf-8'))

            except Exception as e:
                logger.error(f"Socket communication error: {e}")
                # Force close on error to ensure fresh connection next time
                if self.writer:
                    self.writer.close()
                    try:
                        await self.writer.wait_closed()
                    except:
                        pass
                self.writer = None
                return {"error": str(e)}

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
        logger.info("OMNeT++ connection closed.")

# Singleton instance
omnet_client = OmnetClient()