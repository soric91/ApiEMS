"""ConnectionManager: múltiples clientes WebSocket, suscripción por variable.

Cada conexión tiene a lo sumo UNA variable suscrita; cambiar de variable
reemplaza la anterior (el cliente solo recibe la variable activa).
Serialización con orjson.
"""

from typing import Any

import orjson
from fastapi import WebSocket

from app.core.logging import get_logger
from app.models.variables import Variable
from app.schemas.alerts import Alert
from app.schemas.mqtt import DeviceReading

logger = get_logger("apiems.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self._subscriptions: dict[WebSocket, Variable | None] = {}

    @property
    def connection_count(self) -> int:
        return len(self._subscriptions)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._subscriptions[websocket] = None
        logger.info("ws_connected", connections=self.connection_count)

    def disconnect(self, websocket: WebSocket) -> None:
        self._subscriptions.pop(websocket, None)
        logger.info("ws_disconnected", connections=self.connection_count)

    def subscribe(self, websocket: WebSocket, variable: Variable) -> None:
        self._subscriptions[websocket] = variable

    def unsubscribe(self, websocket: WebSocket) -> None:
        self._subscriptions[websocket] = None

    async def send(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        await websocket.send_text(orjson.dumps(payload).decode())

    async def broadcast(self, reading: DeviceReading) -> None:
        """Empuja la lectura a cada cliente suscrito a una variable presente en ella."""
        dead: list[WebSocket] = []
        for websocket, variable in list(self._subscriptions.items()):
            if variable is None or variable.value not in reading.data:
                continue
            payload = _data_message(reading, variable)
            try:
                await self.send(websocket, payload)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(websocket)

    async def broadcast_alert(self, alert: Alert) -> None:
        """A diferencia de broadcast(), llega a TODOS los clientes conectados
        sin importar qué variable tengan suscrita — una alerta importa más
        allá de qué gráfica esté abierta."""
        payload = {"type": "alert", **alert.model_dump(mode="json")}
        dead: list[WebSocket] = []
        for websocket in list(self._subscriptions):
            try:
                await self.send(websocket, payload)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(websocket)


def _data_message(reading: DeviceReading, variable: Variable) -> dict[str, Any]:
    return {
        "type": "data",
        "variable": variable.value,
        "value": reading.data[variable.value],
        # identify_device (UUID por equipo) — mismo valor que usa el filtro
        # de InfluxDB, único en toda la flota (ver RealtimeState.update()).
        "device_id": reading.identify_device,
        "device_name": reading.device_name,
        "timestamp": reading.timestamp.isoformat(),
    }
