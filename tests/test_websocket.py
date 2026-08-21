import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.models.variables import Variable
from app.schemas.alerts import Alert
from app.schemas.mqtt import DeviceReading
from app.services.realtime.state import RealtimeState
from app.services.websocket.manager import ConnectionManager
from tests.conftest import TEST_DEVICE_ID, TEST_TOKEN

READING = DeviceReading(
    device_name="Modbus_DTSU666_11",
    device_id=11,
    identify_device="bf6a469f-4c2a-4402-9438-49a491ad2238",
    device_type="CT_Meter",
    timestamp=datetime(2026, 7, 16, 13, 26, 0, tzinfo=UTC),
    success=True,
    error=None,
    data={"PhV_phsA": 120.4, "TotW": -442.2},
)


def test_subscribe_unknown_variable_returns_error(client: TestClient) -> None:
    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "subscribe", "variable": "NOT_A_VARIABLE"})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "valid_variables" in msg


def test_ping_pong(client: TestClient) -> None:
    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_unknown_action(client: TestClient) -> None:
    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "dance"})
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_subscribe_ack_and_current_value(client: TestClient, app: FastAPI) -> None:
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "subscribe", "variable": "PhV_phsA"})
        ack = ws.receive_json()
        assert ack == {"type": "subscribed", "variable": "PhV_phsA", "device_id": None}
        current = ws.receive_json()
        assert current["type"] == "data"
        assert current["value"] == 120.4
        assert current["device_id"] == "bf6a469f-4c2a-4402-9438-49a491ad2238"


def test_only_subscribed_variable_is_delivered_on_broadcast(
    client: TestClient, app: FastAPI
) -> None:
    manager: ConnectionManager = app.state.ws_manager
    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "subscribe", "variable": "PhV_phsA"})
        ws.receive_json()  # ack (sin valor previo en memoria)

        asyncio.run(manager.broadcast(READING))

        msg = ws.receive_json()
        assert msg["type"] == "data"
        assert msg["variable"] == "PhV_phsA"
        assert msg["value"] == 120.4


def test_unsubscribe_stops_delivery(client: TestClient, app: FastAPI) -> None:
    manager: ConnectionManager = app.state.ws_manager
    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "subscribe", "variable": "PhV_phsA"})
        ws.receive_json()

        ws.send_json({"action": "unsubscribe"})
        assert ws.receive_json() == {"type": "unsubscribed"}

        asyncio.run(manager.broadcast(READING))
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_invalid_json_returns_error(client: TestClient) -> None:
    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_text("not json")
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_non_object_json_returns_error(client: TestClient) -> None:
    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_text("[1, 2, 3]")
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "objeto JSON" in msg["message"]


async def test_broadcast_removes_dead_connection() -> None:
    """Un cliente cuyo send_text() falla (socket caído) se desconecta solo,
    sin tumbar el broadcast para los demás — probado en aislamiento, sin
    pasar por el endpoint real (que ya desconecta limpiamente al cerrar)."""

    class DeadSocket:
        async def accept(self, subprotocol: str | None = None) -> None:
            return None

        async def send_text(self, data: str) -> None:
            raise RuntimeError("connection reset by peer")

    manager = ConnectionManager()
    dead = DeadSocket()
    await manager.connect(dead, frozenset({TEST_DEVICE_ID}))  # pyright: ignore[reportArgumentType]
    manager.subscribe(dead, Variable.VOLTAGE_A)  # pyright: ignore[reportArgumentType]
    assert manager.connection_count == 1

    await manager.broadcast(READING)

    assert manager.connection_count == 0


def test_broadcast_alert_reaches_all_clients_regardless_of_subscription(
    client: TestClient, app: FastAPI
) -> None:
    """A diferencia de broadcast(), una alerta llega a un cliente aunque no
    tenga suscrita la variable en cuestión (o ninguna variable siquiera)."""
    manager: ConnectionManager = app.state.ws_manager
    alert = Alert(
        kind="hourly_power",
        severity="high",
        device_id=TEST_DEVICE_ID,
        variable="TotW",
        value=999.0,
        expected_low=10.0,
        expected_high=50.0,
        bucket=10,
        timestamp=datetime(2026, 4, 20, 10, tzinfo=UTC),
        message="consumo inusual",
    )
    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        # sin suscribirse a ninguna variable
        asyncio.run(manager.broadcast_alert(alert))
        msg = ws.receive_json()
        assert msg["type"] == "alert"
        assert msg["severity"] == "high"
        assert msg["message"] == "consumo inusual"


async def test_broadcast_alert_removes_dead_connection() -> None:
    class DeadSocket:
        async def accept(self, subprotocol: str | None = None) -> None:
            return None

        async def send_text(self, data: str) -> None:
            raise RuntimeError("connection reset by peer")

    manager = ConnectionManager()
    dead = DeadSocket()
    await manager.connect(dead, frozenset({TEST_DEVICE_ID}))  # pyright: ignore[reportArgumentType]
    alert = Alert(
        kind="daily_total",
        severity="moderate",
        # De un equipo de esta flota: una alerta sin dueño ya no llega a nadie,
        # porque no hay forma de decidir quién debería verla.
        device_id=TEST_DEVICE_ID,
        variable="TotWh_import",
        value=1.0,
        expected_low=0.0,
        expected_high=2.0,
        bucket=0,
        timestamp=datetime(2026, 4, 20, tzinfo=UTC),
        message="x",
    )

    await manager.broadcast_alert(alert)

    assert manager.connection_count == 0


# ---------------------------------------------------------------------------
# Aislamiento entre clientes
#
# El estado en memoria y las alertas son de TODA la flota: la ingesta MQTT no
# distingue clientes, y no debería —las alertas tienen que correr aunque no
# haya nadie mirando. El recorte pasa al emitir, y esto es lo que lo verifica.
# ---------------------------------------------------------------------------

AJENO = "00000000-0000-4000-8000-000000000000"

LECTURA_AJENA = DeviceReading(
    device_name="Medidor_De_Otra_Empresa",
    device_id=77,
    identify_device=AJENO,
    device_type="CT_Meter",
    timestamp=datetime(2026, 7, 16, 13, 26, 0, tzinfo=UTC),
    success=True,
    error=None,
    data={"PhV_phsA": 999.9},
)


def test_a_connection_without_a_token_is_closed(client: TestClient) -> None:
    """Un navegador no puede mandar cabeceras en el handshake, así que el
    token va por query string. Sin él, la conexión se cierra."""
    with client.websocket_connect("/ws") as ws, pytest.raises(WebSocketDisconnect):
        ws.receive_json()


def test_a_connection_with_a_bad_token_is_closed(client: TestClient) -> None:
    with (
        client.websocket_connect("/ws?token=no-sirve") as ws,
        pytest.raises(WebSocketDisconnect),
    ):
        ws.receive_json()


def test_another_clients_reading_is_not_delivered(client: TestClient, app: FastAPI) -> None:
    """La lectura entra al estado igual —la alerta la necesita— pero no sale
    hacia un cliente que no es dueño de ese equipo."""
    manager: ConnectionManager = app.state.ws_manager

    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "subscribe", "variable": "PhV_phsA"})
        assert ws.receive_json()["type"] == "subscribed"

        asyncio.run(manager.broadcast(LECTURA_AJENA))
        # La propia sí llega: es la prueba de que el silencio anterior fue el
        # filtro y no que el broadcast esté roto.
        asyncio.run(manager.broadcast(READING))

        msg = ws.receive_json()
        assert msg["device_id"] == TEST_DEVICE_ID
        assert msg["value"] == READING.data["PhV_phsA"]


def test_another_clients_alert_is_not_delivered(client: TestClient, app: FastAPI) -> None:
    manager: ConnectionManager = app.state.ws_manager

    def _alert(device_id: str, message: str) -> Alert:
        return Alert(
            kind="hourly_power",
            severity="high",
            device_id=device_id,
            variable="TotW",
            value=999.0,
            expected_low=10.0,
            expected_high=50.0,
            bucket=10,
            timestamp=datetime(2026, 4, 20, 10, tzinfo=UTC),
            message=message,
        )

    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        asyncio.run(manager.broadcast_alert(_alert(AJENO, "no es tuya")))
        asyncio.run(manager.broadcast_alert(_alert(TEST_DEVICE_ID, "sí es tuya")))

        msg = ws.receive_json()
        assert msg["message"] == "sí es tuya"


def test_the_current_value_on_subscribe_is_also_filtered(client: TestClient, app: FastAPI) -> None:
    """El primer envío tras suscribirse sale del estado en memoria, no del
    broadcast — es el que más fácil se saltea el filtro."""
    state: RealtimeState = app.state.realtime_state
    state.update(LECTURA_AJENA)
    state.update(READING)

    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "subscribe", "variable": "PhV_phsA"})
        assert ws.receive_json()["type"] == "subscribed"

        msg = ws.receive_json()
        assert msg["device_id"] == TEST_DEVICE_ID
        # Y no hay un segundo mensaje con el equipo ajeno.
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"


class TestTheTokenIsNotInTheUrl:
    """Cómo llega la credencial al handshake.

    Estuvo en la query string, y ahí queda escrita en los logs de acceso del
    servidor, en los del proxy y en el historial del navegador. Peor: el
    navegador imprime la URL entera —token incluido— cada vez que una conexión
    falla, que en desarrollo es constantemente.

    Como subprotocolo viaja en `Sec-WebSocket-Protocol`, que es una cabecera.
    """

    def test_the_subprotocol_authenticates(self, client: TestClient) -> None:
        with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
            ws.send_json({"action": "ping"})

            assert ws.receive_json()["type"] == "pong"

    async def test_the_offered_subprotocol_is_echoed_back(self) -> None:
        """Si no se devuelve, el navegador cierra la conexión por su cuenta y
        el código de cierre nunca llega — la falla se ve como una caída de red
        en vez de como un rechazo.

        Se prueba contra el manager y no por HTTP porque el cliente de pruebas
        de Starlette no expone el subprotocolo aceptado: no aplica la regla
        del navegador, así que una aserción por ahí pasaría igual sin eco.
        """

        class Espia:
            def __init__(self) -> None:
                self.subprotocol: str | None = "no-se-llamo"

            async def accept(self, subprotocol: str | None = None) -> None:
                self.subprotocol = subprotocol

        espia = Espia()
        manager = ConnectionManager()

        await manager.connect(
            cast(WebSocket, espia), frozenset({TEST_DEVICE_ID}), subprotocol="bearer"
        )

        assert espia.subprotocol == "bearer"

    def test_a_bad_token_in_the_subprotocol_is_rejected(self, client: TestClient) -> None:
        with client.websocket_connect("/ws", subprotocols=["bearer", "no-es-un-token"]) as ws:
            with pytest.raises(WebSocketDisconnect) as caught:
                ws.receive_json()

            assert caught.value.code == status.WS_1008_POLICY_VIOLATION

    def test_the_subprotocol_without_a_token_is_rejected(self, client: TestClient) -> None:
        """Ofrecer `bearer` y nada más no es una credencial."""
        with client.websocket_connect("/ws", subprotocols=["bearer"]) as ws:
            with pytest.raises(WebSocketDisconnect) as caught:
                ws.receive_json()

            assert caught.value.code == status.WS_1008_POLICY_VIOLATION

    def test_the_url_still_works_for_an_older_client(self, client: TestClient) -> None:
        """Respaldo deliberado: un navegador con la versión anterior cacheada
        no se queda afuera durante el despliegue. Queda un aviso en los
        registros; cuando deje de aparecer, este camino se puede borrar."""
        with client.websocket_connect(f"/ws?token={TEST_TOKEN}") as ws:
            ws.send_json({"action": "ping"})

            assert ws.receive_json()["type"] == "pong"


# --- suscripción acotada a un equipo -------------------------------------
#
# El bug que motivó esto: la suscripción era solo por variable, así que un
# cliente con veinte medidores recibía los veinte para `TotW` y el panel se
# quedaba con el último que llegara. La cifra de la frontera saltaba entre
# medidores sin que nada lo indicara. Con un solo medidor era invisible.

OTRO_EQUIPO = "11111111-2222-4333-8444-555555555555"


def _lectura(device_id: str, valor: float) -> DeviceReading:
    return DeviceReading(
        device_name=f"Medidor {device_id[:4]}",
        device_id=11,
        identify_device=device_id,
        device_type="CT_Meter",
        timestamp=datetime(2026, 7, 16, 13, 26, 0, tzinfo=UTC),
        success=True,
        error=None,
        data={"TotW": valor},
    )


def test_una_suscripcion_con_equipo_solo_recibe_ese_equipo(
    client: TestClient, app: FastAPI, fleet_de_dos: None
) -> None:
    manager: ConnectionManager = app.state.ws_manager

    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "subscribe", "variable": "TotW", "device_id": TEST_DEVICE_ID})
        assert ws.receive_json()["type"] == "subscribed"

        # El ajeno primero: si el filtro no existiera, este llegaría y el test
        # leería su valor creyendo que es el del equipo elegido.
        asyncio.run(manager.broadcast(_lectura(OTRO_EQUIPO, 999.0)))
        asyncio.run(manager.broadcast(_lectura(TEST_DEVICE_ID, 42.0)))

        recibido = ws.receive_json()
        assert recibido["value"] == 42.0
        assert recibido["device_id"] == TEST_DEVICE_ID


def test_sin_equipo_se_siguen_recibiendo_todos(
    client: TestClient, app: FastAPI, fleet_de_dos: None
) -> None:
    """Es lo correcto mientras el panel no eligió medidor: no se le puede
    adivinar cuál quiere, y no mandarle nada lo dejaría en blanco."""
    manager: ConnectionManager = app.state.ws_manager

    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "subscribe", "variable": "TotW"})
        assert ws.receive_json()["device_id"] is None

        asyncio.run(manager.broadcast(_lectura(OTRO_EQUIPO, 999.0)))
        asyncio.run(manager.broadcast(_lectura(TEST_DEVICE_ID, 42.0)))

        assert [ws.receive_json()["value"] for _ in range(2)] == [999.0, 42.0]


def test_un_equipo_de_otra_empresa_se_rechaza(client: TestClient) -> None:
    """Y no se ignora en silencio: el panel quedaría esperando datos que nunca
    van a llegar, que se ve igual que un medidor apagado."""
    ajeno = "99999999-8888-4777-8666-555555555555"

    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "subscribe", "variable": "TotW", "device_id": ajeno})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "empresa" in msg["message"]


def test_el_valor_actual_inmediato_tambien_se_acota(
    client: TestClient, app: FastAPI, fleet_de_dos: None
) -> None:
    """Al suscribirse se manda el último valor conocido para no esperar al
    próximo mensaje MQTT. Ese envío también tiene que respetar el equipo — si
    no, el primer número que ve el panel sería de otro medidor."""
    state: RealtimeState = app.state.realtime_state
    state.update(_lectura(OTRO_EQUIPO, 999.0))
    state.update(_lectura(TEST_DEVICE_ID, 42.0))

    with client.websocket_connect("/ws", subprotocols=["bearer", TEST_TOKEN]) as ws:
        ws.send_json({"action": "subscribe", "variable": "TotW", "device_id": TEST_DEVICE_ID})
        assert ws.receive_json()["type"] == "subscribed"

        actual = ws.receive_json()
        assert actual["device_id"] == TEST_DEVICE_ID
        assert actual["value"] == 42.0
