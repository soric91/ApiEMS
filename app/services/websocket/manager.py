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
    """Conexiones abiertas, cada una con su variable y su flota.

    La flota se guarda por conexión, no se consulta al emitir: una lectura
    llega cada pocos segundos y resolverla contra el CRM en cada envío sería
    una petición HTTP por mensaje por cliente.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[WebSocket, Variable | None] = {}
        # Qué equipos puede ver cada conexión. Sin entrada = no ve ninguno,
        # que es el valor correcto para una conexión a medio establecer.
        self._visible: dict[WebSocket, frozenset[str]] = {}

    @property
    def connection_count(self) -> int:
        return len(self._subscriptions)

    async def connect(
        self,
        websocket: WebSocket,
        devices: frozenset[str],
        *,
        subprotocol: str | None = None,
    ) -> None:
        # El subprotocolo se devuelve tal como lo ofreció el cliente. Omitirlo
        # cuando ofreció uno hace que el navegador cierre la conexión.
        await websocket.accept(subprotocol=subprotocol)
        self._subscriptions[websocket] = None
        self._visible[websocket] = devices
        logger.info(
            "ws_connected", connections=self.connection_count, devices=len(devices)
        )

    def disconnect(self, websocket: WebSocket) -> None:
        self._subscriptions.pop(websocket, None)
        self._visible.pop(websocket, None)
        logger.info("ws_disconnected", connections=self.connection_count)

    def may_see(self, websocket: WebSocket, device_id: str | None) -> bool:
        """Si esta conexión tiene permitido ver ese equipo.

        Un `device_id` vacío no se difunde a nadie: sin saber de quién es, no
        hay forma de decidir quién debería verlo.
        """
        if device_id is None:
            return False
        return device_id in self._visible.get(websocket, frozenset())

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
            if not self.may_see(websocket, reading.identify_device):
                continue
            payload = _data_message(reading, variable)
            try:
                await self.send(websocket, payload)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(websocket)

    async def broadcast_alert(self, alert: Alert) -> None:
        """Llega sin importar qué variable tenga suscrita cada cliente — una
        alerta importa más allá de qué gráfica esté abierta.

        Lo que sí se respeta es de quién es el equipo: una alerta es un dato
        de consumo como cualquier otro.
        """
        payload = {"type": "alert", **alert.model_dump(mode="json")}
        dead: list[WebSocket] = []
        for websocket in list(self._subscriptions):
            if not self.may_see(websocket, alert.device_id):
                continue
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
