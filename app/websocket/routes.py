"""Endpoint WebSocket /ws.

Protocolo (JSON):
- Cliente → servidor:
    {"action": "subscribe", "variable": "POWER_ACTIVE_INST_TOTAL"}
    {"action": "unsubscribe"}
    {"action": "ping"}
- Servidor → cliente:
    {"type": "subscribed", "variable": ...}   ack + valor actual si existe
    {"type": "data", "variable", "value", "device_id", "device_name", "timestamp"}
    {"type": "unsubscribed"} | {"type": "pong"} | {"type": "error", "message"}
"""

from typing import Any, cast

import orjson
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models.variables import Variable
from app.services.realtime.state import RealtimeState
from app.services.websocket.manager import ConnectionManager

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    manager = cast(ConnectionManager, websocket.app.state.ws_manager)
    state = cast(RealtimeState, websocket.app.state.realtime_state)

    await manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_message(manager, state, websocket, raw)
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def _handle_message(
    manager: ConnectionManager,
    state: RealtimeState,
    websocket: WebSocket,
    raw: str,
) -> None:
    try:
        message: Any = orjson.loads(raw)
    except orjson.JSONDecodeError:
        await manager.send(websocket, {"type": "error", "message": "JSON inválido"})
        return
    if not isinstance(message, dict):
        await manager.send(websocket, {"type": "error", "message": "Se esperaba un objeto JSON"})
        return
    payload = cast(dict[str, Any], message)

    action = payload.get("action")

    if action == "subscribe":
        raw_variable = payload.get("variable")
        try:
            variable = Variable(raw_variable)
        except ValueError:
            await manager.send(
                websocket,
                {
                    "type": "error",
                    "message": f"Variable desconocida: {raw_variable!r}",
                    "valid_variables": [v.value for v in Variable],
                },
            )
            return
        manager.subscribe(websocket, variable)
        await manager.send(websocket, {"type": "subscribed", "variable": variable.value})
        # Valor actual inmediato para que el cliente no espere al próximo mensaje MQTT
        for snapshot in state.values_of(variable.value):
            await manager.send(
                websocket,
                {
                    "type": "data",
                    "variable": variable.value,
                    "value": snapshot.data[variable.value],
                    "device_id": snapshot.device_id,
                    "device_name": snapshot.device_name,
                    "timestamp": snapshot.timestamp.isoformat(),
                },
            )
        return

    if action == "unsubscribe":
        manager.unsubscribe(websocket)
        await manager.send(websocket, {"type": "unsubscribed"})
        return

    if action == "ping":
        await manager.send(websocket, {"type": "pong"})
        return

    await manager.send(websocket, {"type": "error", "message": f"Acción desconocida: {action!r}"})
