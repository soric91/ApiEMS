import asyncio
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.variables import Variable
from app.schemas.alerts import Alert
from app.schemas.mqtt import DeviceReading
from app.services.realtime.state import RealtimeState
from app.services.websocket.manager import ConnectionManager

READING = DeviceReading(
    device_name="Modbus_DTSU666_11",
    device_id=11,
    identify_device="bf6a469f-4c2a-4402-9438-49a491ad2238",
    device_type="CT_Meter",
    timestamp=datetime(2026, 7, 16, 13, 26, 0, tzinfo=UTC),
    success=True,
    error=None,
    data={"VOLTAGE_A": 120.4, "POWER_ACTIVE_INST_TOTAL": -442.2},
)


def test_subscribe_unknown_variable_returns_error(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "subscribe", "variable": "NOT_A_VARIABLE"})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "valid_variables" in msg


def test_ping_pong(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_unknown_action(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "dance"})
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_subscribe_ack_and_current_value(client: TestClient, app: FastAPI) -> None:
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "subscribe", "variable": "VOLTAGE_A"})
        ack = ws.receive_json()
        assert ack == {"type": "subscribed", "variable": "VOLTAGE_A"}
        current = ws.receive_json()
        assert current["type"] == "data"
        assert current["value"] == 120.4
        assert current["device_id"] == "bf6a469f-4c2a-4402-9438-49a491ad2238"


def test_only_subscribed_variable_is_delivered_on_broadcast(
    client: TestClient, app: FastAPI
) -> None:
    manager: ConnectionManager = app.state.ws_manager
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "subscribe", "variable": "VOLTAGE_A"})
        ws.receive_json()  # ack (sin valor previo en memoria)

        asyncio.run(manager.broadcast(READING))

        msg = ws.receive_json()
        assert msg["type"] == "data"
        assert msg["variable"] == "VOLTAGE_A"
        assert msg["value"] == 120.4


def test_unsubscribe_stops_delivery(client: TestClient, app: FastAPI) -> None:
    manager: ConnectionManager = app.state.ws_manager
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "subscribe", "variable": "VOLTAGE_A"})
        ws.receive_json()

        ws.send_json({"action": "unsubscribe"})
        assert ws.receive_json() == {"type": "unsubscribed"}

        asyncio.run(manager.broadcast(READING))
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_invalid_json_returns_error(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text("not json")
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_non_object_json_returns_error(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text("[1, 2, 3]")
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "objeto JSON" in msg["message"]


async def test_broadcast_removes_dead_connection() -> None:
    """Un cliente cuyo send_text() falla (socket caído) se desconecta solo,
    sin tumbar el broadcast para los demás — probado en aislamiento, sin
    pasar por el endpoint real (que ya desconecta limpiamente al cerrar)."""

    class DeadSocket:
        async def send_text(self, data: str) -> None:
            raise RuntimeError("connection reset by peer")

    manager = ConnectionManager()
    dead = DeadSocket()
    manager._subscriptions[dead] = Variable.VOLTAGE_A  # pyright: ignore[reportPrivateUsage, reportArgumentType]
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
        device_id="11",
        variable="POWER_ACTIVE_INST_TOTAL",
        value=999.0,
        expected_low=10.0,
        expected_high=50.0,
        bucket=10,
        timestamp=datetime(2026, 4, 20, 10, tzinfo=UTC),
        message="consumo inusual",
    )
    with client.websocket_connect("/ws") as ws:
        # sin suscribirse a ninguna variable
        asyncio.run(manager.broadcast_alert(alert))
        msg = ws.receive_json()
        assert msg["type"] == "alert"
        assert msg["severity"] == "high"
        assert msg["message"] == "consumo inusual"


async def test_broadcast_alert_removes_dead_connection() -> None:
    class DeadSocket:
        async def send_text(self, data: str) -> None:
            raise RuntimeError("connection reset by peer")

    manager = ConnectionManager()
    dead = DeadSocket()
    manager._subscriptions[dead] = None  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    alert = Alert(
        kind="daily_total",
        severity="moderate",
        device_id=None,
        variable="POWER_ACTIVE_TOTAL_POS",
        value=1.0,
        expected_low=0.0,
        expected_high=2.0,
        bucket=0,
        timestamp=datetime(2026, 4, 20, tzinfo=UTC),
        message="x",
    )

    await manager.broadcast_alert(alert)

    assert manager.connection_count == 0
