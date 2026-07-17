from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.mqtt import DeviceReading
from app.services.realtime.state import RealtimeState

READING = DeviceReading(
    device_name="Modbus_DTSU666_11",
    device_id=11,
    identify_device="bf6a469f-4c2a-4402-9438-49a491ad2238",
    device_type="CT_Meter",
    timestamp=datetime(2026, 7, 16, 13, 26, 0, tzinfo=UTC),
    success=True,
    error=None,
    data={"VOLTAGE_A": 120.4},
)


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"username": "testuser", "password": "testpass"}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_realtime_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/realtime/latest").status_code == 401


def test_latest_empty_then_populated(client: TestClient, app: FastAPI) -> None:
    headers = _login(client)
    empty = client.get("/api/v1/realtime/latest", headers=headers)
    assert empty.json()["data"] == []

    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    populated = client.get("/api/v1/realtime/latest", headers=headers)
    assert len(populated.json()["data"]) == 1


def test_device_not_found(client: TestClient) -> None:
    headers = _login(client)
    response = client.get("/api/v1/realtime/device", params={"device_id": "99"}, headers=headers)
    assert response.status_code == 404


def test_device_found(client: TestClient, app: FastAPI) -> None:
    headers = _login(client)
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    response = client.get("/api/v1/realtime/device", params={"device_id": "11"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["device_name"] == "Modbus_DTSU666_11"
