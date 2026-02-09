import asyncio
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OmnetUdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, pending_futures):
        self.pending_futures = pending_futures
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        logger.info("UDP transport established")

    def datagram_received(self, data, addr):
        try:
            text = data.decode('utf-8').strip()
            if not text:
                return
            
            payload = json.loads(text)
            
            if not self.pending_futures.empty():
                future = self.pending_futures.get_nowait()
                if not future.done():
                    future.set_result(payload)
            else:
                logger.warning(f"Received unsolicited data: {payload}")
        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON: {data}")
        except Exception as e:
            logger.error(f"Error processing UDP response: {e}")

    def error_received(self, exc):
        logger.error(f"UDP listener error: {exc}")

    def connection_lost(self, exc):
        if exc:
            logger.error(f"UDP connection lost: {exc}")
        else:
            logger.info("UDP connection closed")


class OmnetClient:
    def __init__(self, host: str = "192.168.20.51", port: int = 9999):
        self.host = host
        self.port = port
        self.transport = None
        self.protocol = None
        self.pending_futures = asyncio.Queue()

    async def connect(self):
        """Establishes an async UDP connection to the OMNeT++ bridge."""
        logger.info(f"Attempting to connect to OMNeT++ (UDP) at {self.host}:{self.port}...")
        loop = asyncio.get_running_loop()
        try:
            self.transport, self.protocol = await loop.create_datagram_endpoint(
                lambda: OmnetUdpProtocol(self.pending_futures),
                remote_addr=(self.host, self.port)
            )
            logger.info("Successfully connected to OMNeT++ bridge (UDP).")
        except Exception as e:
            logger.error(f"Failed to connect to OMNeT++: {e}")
            self.transport = None
            self.protocol = None

    async def send_and_receive(self, data: dict) -> dict:
        """Sends data and waits for the corresponding response (FIFO)."""
        if self.transport is None or self.transport.is_closing():
            logger.warning("Socket is not connected. Attempting to reconnect...")
            await self.connect()
            if self.transport is None:
                return {"error": "Connection unavailable"}

        # Create a future to await the response
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.pending_futures.put(future)

        try:
            # Send data with newline delimiter for framing
            message = json.dumps(data).encode('utf-8') + b'\n'
            self.transport.sendto(message)

            # Wait for the response from the protocol callback
            return await future

        except Exception as e:
            logger.error(f"Socket communication error: {e}")
            if not future.done():
                future.cancel()
            return {"error": str(e)}

    async def close(self):
        """Closes the socket connection."""
        # Cancel all pending futures
        while not self.pending_futures.empty():
            f = self.pending_futures.get_nowait()
            if not f.done():
                f.cancel()

        if self.transport:
            self.transport.close()
            self.transport = None
            self.protocol = None
            logger.info("OMNeT++ connection closed.")

# Singleton instance
omnet_client = OmnetClient()