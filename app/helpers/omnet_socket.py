import asyncio
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OmnetClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9999):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self.pending_futures = asyncio.Queue()
        self.listen_task = None

    async def connect(self):
        """Establishes an async TCP connection to the OMNeT++ bridge."""
        logger.info(f"Attempting to connect to OMNeT++ at {self.host}:{self.port}...")
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            # Start background listener for full-duplex reading
            self.listen_task = asyncio.create_task(self._read_loop())
            logger.info("Successfully connected to OMNeT++ bridge.")
        except Exception as e:
            logger.error(f"Failed to connect to OMNeT++: {e}")
            self.reader = None
            self.writer = None

    async def _read_loop(self):
        """Continuously reads from the socket and resolves pending futures."""
        try:
            while True:
                # Read line-by-line (assumes newline-delimited JSON for framing)
                line = await self.reader.readline()
                if not line:
                    logger.warning("Connection closed by server (EOF).")
                    break
                
                try:
                    data = json.loads(line.decode('utf-8'))
                    # Match response to the oldest pending request (FIFO)
                    if not self.pending_futures.empty():
                        future = await self.pending_futures.get()
                        if not future.done():
                            future.set_result(data)
                    else:
                        logger.warning(f"Received unsolicited data: {data}")
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON: {line}")
                except Exception as e:
                    logger.error(f"Error processing response: {e}")

        except Exception as e:
            logger.error(f"Read loop error: {e}")
        finally:
            # Ensure cleanup if loop exits
            if self.writer:
                await self.close()

    async def send_and_receive(self, data: dict) -> dict:
        """Sends data and waits for the corresponding response (FIFO)."""
        if self.writer is None or self.writer.is_closing():
            logger.warning("Socket is not connected. Attempting to reconnect...")
            await self.connect()
            if self.writer is None:
                return {"error": "Connection unavailable"}

        # Create a future to await the response
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.pending_futures.put(future)

        try:
            # Send data with newline delimiter for framing
            message = json.dumps(data).encode('utf-8') + b'\n'
            self.writer.write(message)
            await self.writer.drain()

            # Wait for the response from the read loop
            return await future

        except Exception as e:
            logger.error(f"Socket communication error: {e}")
            if not future.done():
                future.cancel()
            return {"error": str(e)}

    async def close(self):
        """Closes the socket connection."""
        if self.listen_task:
            self.listen_task.cancel()
            try:
                await self.listen_task
            except asyncio.CancelledError:
                pass
            self.listen_task = None

        # Cancel all pending futures
        while not self.pending_futures.empty():
            f = self.pending_futures.get_nowait()
            if not f.done():
                f.cancel()

        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                logger.error(f"Error closing socket: {e}")
            finally:
                self.reader = None
                self.writer = None
                logger.info("OMNeT++ connection closed.")

# Singleton instance
omnet_client = OmnetClient()