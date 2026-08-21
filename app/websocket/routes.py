"""Endpoint WebSocket /ws.

Protocolo (JSON):
- Cliente → servidor:
    {"action": "subscribe", "variable": "TotW", "device_id": "<uuid>"}  device_id opcional
    {"action": "unsubscribe"}
    {"action": "ping"}
- Servidor → cliente:
    {"type": "subscribed", "variable": ...}   ack + valor actual si existe
    {"type": "data", "variable", "value", "device_id", "device_name", "timestamp"}
    {"type": "unsubscribed"} | {"type": "pong"} | {"type": "error", "message"}
"""

from typing import Any, cast

import orjson
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.core.crm_identity import CrmIdentityVerifier, InvalidIdentityError
from app.core.logging import get_logger
from app.models.variables import Variable
from app.services.crm.client import CrmClientError
from app.services.crm.fleet import FleetDirectory
from app.services.realtime.state import RealtimeState
from app.services.websocket.manager import ConnectionManager

logger = get_logger("apiems.ws")

router = APIRouter()

# 1008 = Policy Violation. El cierre estándar para "te conectaste, pero no
# tenés permitido estar acá".
_POLICY_VIOLATION = status.WS_1008_POLICY_VIOLATION


# El subprotocolo con el que el cliente anuncia que trae credencial. El
# handshake manda `Sec-WebSocket-Protocol: bearer, <token>`, y la respuesta
# tiene que devolver uno de los ofrecidos o el navegador cierra la conexión.
_BEARER = "bearer"
# `bearer` más el token. Menos de dos valores significa que el cliente ofreció
# el subprotocolo pero no la credencial.
_PARTES_ESPERADAS = 2


def _token_de(websocket: WebSocket) -> str | None:
    """El token del handshake, del subprotocolo o de la URL.

    Un navegador no puede poner cabeceras propias en el handshake de un
    WebSocket, pero sí ofrecer subprotocolos, y esos viajan en
    `Sec-WebSocket-Protocol` — una cabecera, no la URL. Importa porque las
    query strings quedan escritas en los logs de acceso del servidor, en los
    del proxy y en el historial del navegador, y porque el navegador imprime
    la URL entera cada vez que una conexión falla.

    La URL se sigue aceptando como respaldo para no cortar a un cliente con
    la versión anterior cacheada. Queda registrado cuando pasa: cuando el
    aviso deje de aparecer, este camino se puede borrar.
    """
    ofrecidos = [
        parte.strip()
        for parte in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if parte.strip()
    ]
    if len(ofrecidos) >= _PARTES_ESPERADAS and ofrecidos[0] == _BEARER:
        return ofrecidos[1]

    token = websocket.query_params.get("token")
    if token:
        logger.warning("ws_token_en_url", detail="cliente viejo: token expuesto en la URL")
    return token


async def _authorize(websocket: WebSocket) -> frozenset[str] | None:
    """Los equipos que esta conexión puede ver, o None si no debe abrirse.

    Es el mismo token que el CRM emitió, verificado igual que en cualquier
    otra ruta.
    """
    token = _token_de(websocket)
    if not token:
        return None

    verifier = cast(CrmIdentityVerifier, websocket.app.state.identity_verifier)
    directory = cast(FleetDirectory, websocket.app.state.fleet_directory)
    try:
        identity = verifier.verify(token)
    except InvalidIdentityError as exc:
        logger.info("ws_rejected", reason=exc.reason)
        return None

    if identity.must_change_password or identity.client_id is None:
        logger.info("ws_rejected", reason="token restringido o sin empresa")
        return None

    try:
        fleet = await directory.for_client(identity.client_id)
    except CrmClientError:
        logger.warning("ws_rejected", reason="flota no disponible")
        return None

    if not fleet.puede_ver_consumo and not identity.impersonated:
        # Misma excepción que en las rutas HTTP: la marca decide lo que ve el
        # cliente, no quien lo administra. Sin esto, un administrador
        # revisando una empresa con el consumo apagado veía los datos
        # históricos y no el tiempo real — la mitad del panel funcionando.
        logger.info("ws_rejected", reason="consumo deshabilitado")
        return None
    return fleet.device_ids


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    manager = cast(ConnectionManager, websocket.app.state.ws_manager)
    state = cast(RealtimeState, websocket.app.state.realtime_state)

    # Si el cliente ofreció el subprotocolo hay que devolvérselo, incluso al
    # rechazar: un navegador que no ve confirmado ninguno de los que ofreció
    # cierra la conexión él mismo, y el código de cierre nunca llega.
    subprotocol = (
        _BEARER if _BEARER in websocket.headers.get("sec-websocket-protocol", "") else None
    )

    devices = await _authorize(websocket)
    if devices is None:
        # Aceptar y cerrar, en vez de rechazar el handshake: así el cliente
        # recibe un código de cierre que puede distinguir de una caída de red.
        await websocket.accept(subprotocol=subprotocol)
        await websocket.close(code=_POLICY_VIOLATION)
        return

    await manager.connect(websocket, devices, subprotocol=subprotocol)
    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_message(manager, state, websocket, raw)
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def _suscribir(
    manager: ConnectionManager,
    state: RealtimeState,
    websocket: WebSocket,
    payload: dict[str, Any],
) -> None:
    """Atiende `subscribe`. Vive aparte porque tiene varias salidas distintas.

    Cada `return` es un rechazo con su propio motivo —variable desconocida,
    equipo ajeno— y colapsarlos en uno solo haría que el panel no pueda
    distinguir qué corregir.
    """
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
    # Acotar a un equipo es opcional: sin él se sigue recibiendo todo lo
    # que el cliente puede ver, que es lo correcto mientras no eligió
    # medidor. Un equipo ajeno se rechaza acá y no se ignora en silencio:
    # el panel quedaría esperando datos que nunca van a llegar.
    raw_device = payload.get("device_id")
    device_id = str(raw_device) if raw_device else None
    if device_id is not None and not manager.may_see(websocket, device_id):
        await manager.send(
            websocket,
            {"type": "error", "message": "Ese equipo no es de esta empresa"},
        )
        return

    manager.subscribe(websocket, variable, device_id)
    await manager.send(
        websocket,
        {"type": "subscribed", "variable": variable.value, "device_id": device_id},
    )
    # Valor actual inmediato para que el cliente no espere al próximo
    # mensaje MQTT. Pasa por el mismo filtro que broadcast(): el estado en
    # memoria es de toda la flota, y sin este chequeo el primer envío tras
    # suscribirse sería el único que se saltea el recorte por cliente.
    for snapshot in state.values_of(variable.value):
        if not manager.may_see(websocket, snapshot.device_id):
            continue
        if device_id is not None and snapshot.device_id != device_id:
            continue
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
        await _suscribir(manager, state, websocket, payload)
        return

    if action == "unsubscribe":
        manager.unsubscribe(websocket)
        await manager.send(websocket, {"type": "unsubscribed"})
        return

    if action == "ping":
        await manager.send(websocket, {"type": "pong"})
        return

    await manager.send(websocket, {"type": "error", "message": f"Acción desconocida: {action!r}"})
