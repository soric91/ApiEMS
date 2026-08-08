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
    data={"PhV_phsA": 120.4},
)


def test_realtime_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/realtime/latest").status_code == 401


def test_latest_empty_then_populated(
    client: TestClient, app: FastAPI, auth_headers: dict[str, str]
) -> None:
    empty = client.get("/api/v1/realtime/latest", headers=auth_headers)
    assert empty.json()["data"] == []

    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    populated = client.get("/api/v1/realtime/latest", headers=auth_headers)
    assert len(populated.json()["data"]) == 1


def test_device_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/realtime/device", params={"device_id": "99"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_device_found(client: TestClient, app: FastAPI, auth_headers: dict[str, str]) -> None:
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    response = client.get(
        "/api/v1/realtime/device",
        params={"device_id": "bf6a469f-4c2a-4402-9438-49a491ad2238"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["device_name"] == "Modbus_DTSU666_11"
