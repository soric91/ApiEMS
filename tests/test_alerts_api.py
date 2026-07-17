from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.variables import Variable
from app.schemas.alerts import Alert
from app.schemas.influx import EnergyPoint
from app.services.alerts.state import AlertsState
from tests.fakes import FakeInfluxRepository


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"username": "testuser", "password": "testpass"}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _alert() -> Alert:
    return Alert(
        kind="hourly_power",
        severity="high",
        device_id="11",
        variable="POWER_ACTIVE_INST_TOTAL",
        value=999.0,
        expected_low=10.0,
        expected_high=50.0,
        bucket=10,
        timestamp=datetime(2026, 4, 20, 10, tzinfo=UTC),
        message="test alert",
    )


def test_alerts_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/alerts").status_code == 401


def test_alerts_returns_recent_from_state(client: TestClient, app: FastAPI) -> None:
    headers = _login(client)
    state: AlertsState = app.state.alerts_state
    state.add(_alert())

    response = client.get("/api/v1/alerts", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert len(body["recent"]) == 1
    assert body["recent"][0]["severity"] == "high"


def test_alerts_daily_total_null_with_degenerate_band(
    client: TestClient, fake_influx_repo: FakeInfluxRepository
) -> None:
    """Banda degenerada (todas las muestras iguales) en los 7 días de semana
    -> daily_total siempre None, sin importar qué día real sea "ayer" cuando
    corre el test (evita que el test dependa de la fecha de ejecución)."""
    headers = _login(client)
    monday = datetime(2026, 3, 2, tzinfo=UTC)  # lunes de referencia
    points: list[EnergyPoint] = []
    for weekday_offset in range(7):
        day = monday + timedelta(days=weekday_offset)
        for week in range(3):
            points.append(EnergyPoint(time=day + timedelta(weeks=week), value=10.0))
    fake_influx_repo.energy_series_by_counter = {Variable.POWER_ACTIVE_TOTAL_POS: points}

    response = client.get("/api/v1/alerts", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["daily_total"] is None


def test_alerts_empty_when_no_history(client: TestClient) -> None:
    headers = _login(client)
    response = client.get("/api/v1/alerts", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["recent"] == []
    assert body["daily_total"] is None
