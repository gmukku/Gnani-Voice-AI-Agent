"""WebSocket fan-out for live dashboard updates (bonus requirement).

Deliberately fire-and-forget: a dashboard that has gone away must never cause a
webhook to fail, so every send is wrapped and dead sockets are pruned rather
than raised.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

from app.utils.logging import get_logger

log = get_logger(__name__)


class ConnectionHub:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        log.info("ws.connected", clients=len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        log.info("ws.disconnected", clients=len(self._connections))

    async def broadcast(self, event: str, payload: dict[str, Any]) -> None:
        """Send to every live client, dropping any that error."""
        async with self._lock:
            targets = list(self._connections)

        if not targets:
            return

        message = {"event": event, "data": payload}
        dead: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_json(message)
            except Exception:  # noqa: BLE001 - a dead socket must not propagate
                dead.append(websocket)

        if dead:
            async with self._lock:
                for websocket in dead:
                    self._connections.discard(websocket)
            log.info("ws.pruned", removed=len(dead))


hub = ConnectionHub()
