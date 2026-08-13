"""Tests HTTP de /dashboard/status.

El panel principal y /dashboard/cards ya no existen (fase V3): el contrato
vigente es /dashboard/summary (ver test_dashboard_summary_api.py) más la
conectividad de /dashboard/status, que es lo que queda acá.
"""

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
    data={
        "PhV_phsA": 120.4,
        "PhV_phsB": 121.2,
        "A_phsA": 1.93,
        "A_phsB": 2.81,
        "TotW": -442.2,
        "TotPF": 0.75,
    },
)


def test_dashboard_status_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/dashboard/status").status_code == 401


def test_dashboard_status_reports_connectivity(
    client: TestClient, app: FastAPI, auth_headers: dict[str, str]
) -> None:
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    response = client.get("/api/v1/dashboard/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["devices_total"] == 1
    assert body["devices_online"] == 1
    assert body["mqtt_connected"] is False  # MQTTService.start() no corre en testing
    assert body["last_message_at"] is not None
