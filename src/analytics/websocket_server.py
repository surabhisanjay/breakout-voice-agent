import asyncio
import json
import logging
import threading
from typing import Any, Optional, Set
import websockets

logger = logging.getLogger(__name__)

CLIENTS: Set[Any] = set()

class WebSocketServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8030) -> None:
        self.host = host
        self.port = port
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Starts the WebSocket server in a daemonized background thread."""
        if self.loop and self.loop.is_running():
            logger.info("WebSocket broker already has an active event loop")
            return
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info(f"WebSocket broker service thread initiated on {self.host}:{self.port}")

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Use the FastAPI event loop for native /ws clients and broadcasts."""
        self.loop = loop
        logger.info("WebSocket broker attached to FastAPI event loop")

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        async def handler(websocket) -> None:
            CLIENTS.add(websocket)
            logger.info(f"New dashboard client connected: {websocket.remote_address}")
            try:
                async for message in websocket:
                    pass
            except websockets.ConnectionClosed:
                pass
            finally:
                CLIENTS.remove(websocket)
                logger.info(f"Dashboard client disconnected: {websocket.remote_address}")

        async def main():
            async with websockets.serve(handler, self.host, self.port):
                await asyncio.Future()

        self.loop.run_until_complete(main())

    def broadcast(self, event_type: str, data: dict) -> None:
        """Thread-safe event broadcast to all active websocket sessions."""
        if not self.loop or not self.loop.is_running():
            logger.warning(
                "WebSocket broadcast skipped event_type=%s reason=broker_not_running",
                event_type,
            )
            return
        
        payload = json.dumps({"event_type": event_type, "payload": data})
        
        async def do_broadcast() -> None:
            logger.info(
                "WebSocket broadcasting event_type=%s clients=%s session_id=%s",
                event_type,
                len(CLIENTS),
                data.get("session_id", ""),
            )
            if CLIENTS:
                await asyncio.gather(*[c.send(payload) for c in CLIENTS], return_exceptions=True)

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is self.loop:
            self.loop.create_task(do_broadcast())
        else:
            asyncio.run_coroutine_threadsafe(do_broadcast(), self.loop)

# Singleton global instance
ws_server = WebSocketServer()
