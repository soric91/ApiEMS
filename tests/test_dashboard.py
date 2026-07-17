from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.mqtt import DeviceReading
from app.services.realtime.state import RealtimeState
from tests.fakes import FakeInfluxRepository

READING = DeviceReading(
    device_name="Modbus_DTSU666_11",
    device_id=11,
    identify_device="bf6a469f-4c2a-4402-9438-49a491ad2238",
    device_type="CT_Meter",
    timestamp=datetime(2026, 7, 16, 13, 26, 0, tzinfo=UTC),
    success=True,
    error=None,
    data={
        "VOLTAGE_A": 120.4,
        "VOLTAGE_B": 121.2,
        "CURRENT_A": 1.93,
        "CURRENT_B": 2.81,
        "POWER_ACTIVE_INST_TOTAL": -442.2,
        "FACTOR_POTENCIA_TOTAL": 0.75,
    },
)


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"username": "testuser", "password": "testpass"}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_dashboard_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/dashboard").status_code == 401


def test_dashboard_503_without_realtime_data(client: TestClient) -> None:
    headers = _login(client)
    response = client.get("/api/v1/dashboard", headers=headers)
    assert response.status_code == 503


def test_dashboard_returns_instant_plus_energy(
    client: TestClient, app: FastAPI, fake_influx_repo: FakeInfluxRepository
) -> None:
    headers = _login(client)
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    fake_influx_repo.energy_total_value = 3.2

    response = client.get("/api/v1/dashboard", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["device_id"] == "11"
    assert data["power_active_total_w"] == -442.2
    assert data["voltage_a"] == 120.4
    assert data["consumption_today_kwh"] == 3.2
    assert data["export_month_kwh"] == 3.2


def test_dashboard_unknown_device_id_404(client: TestClient, app: FastAPI) -> None:
    headers = _login(client)
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    response = client.get("/api/v1/dashboard", params={"device_id": "99"}, headers=headers)
    assert response.status_code == 404


def test_dashboard_explicit_device_id_found(client: TestClient, app: FastAPI) -> None:
    headers = _login(client)
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    response = client.get("/api/v1/dashboard", params={"device_id": "11"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["device_id"] == "11"


def test_dashboard_cards_shape(client: TestClient, app: FastAPI) -> None:
    headers = _login(client)
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    response = client.get("/api/v1/dashboard/cards", headers=headers)
    assert response.status_code == 200
    cards = response.json()["data"]
    keys = {card["key"] for card in cards}
    assert "power" in keys
    assert "consumption_today" in keys
    assert all({"key", "label", "value", "unit"} <= card.keys() for card in cards)


def test_dashboard_status_reports_connectivity(client: TestClient, app: FastAPI) -> None:
    headers = _login(client)
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    response = client.get("/api/v1/dashboard/status", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["devices_total"] == 1
    assert body["devices_online"] == 1
    assert body["mqtt_connected"] is False  # MQTTService.start() no corre en testing
    assert body["last_message_at"] is not None
