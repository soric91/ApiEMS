from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.schemas.influx import EnergyPoint, TimeSeriesPoint
from tests.fakes import FakeInfluxRepository

FROM = "2026-07-01T00:00:00Z"
TO = "2026-07-02T00:00:00Z"


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"username": "testuser", "password": "testpass"}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_history_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/history", params={"variable": "VOLTAGE_A"}).status_code == 401


def test_history_instant_variable(
    client: TestClient, fake_influx_repo: FakeInfluxRepository
) -> None:
    headers = _login(client)
    fake_influx_repo.instant_series_points = [
        TimeSeriesPoint(time=datetime(2026, 7, 1, 12, tzinfo=UTC), value=120.5)
    ]
    response = client.get(
        "/api/v1/history",
        params={"variable": "VOLTAGE_A", "from": FROM, "to": TO, "aggregation": "max"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["aggregation"] == "max"
    assert body["points"][0]["value"] == 120.5
    assert fake_influx_repo.calls[-1][0] == "instant_series"


def test_history_counter_variable_uses_difference(
    client: TestClient, fake_influx_repo: FakeInfluxRepository
) -> None:
    headers = _login(client)
    fake_influx_repo.energy_series_points = [
        EnergyPoint(time=datetime(2026, 7, 1, 12, tzinfo=UTC), value=0.5)
    ]
    response = client.get(
        "/api/v1/history",
        params={"variable": "POWER_ACTIVE_TOTAL_POS", "from": FROM, "to": TO},
        headers=headers,
    )
    assert response.status_code == 200
    assert fake_influx_repo.calls[-1][0] == "energy_series"
    assert response.json()["data"]["points"][0]["value"] == 0.5


def test_history_invalid_range_rejected(client: TestClient) -> None:
    headers = _login(client)
    response = client.get(
        "/api/v1/history",
        params={"variable": "VOLTAGE_A", "from": TO, "to": FROM},
        headers=headers,
    )
    assert response.status_code == 400


def test_history_too_many_points_rejected(client: TestClient) -> None:
    headers = _login(client)
    response = client.get(
        "/api/v1/history",
        params={
            "variable": "VOLTAGE_A",
            "from": "2020-01-01T00:00:00Z",
            "to": "2026-01-01T00:00:00Z",
            "interval_seconds": 1,
        },
        headers=headers,
    )
    assert response.status_code == 400


def test_history_downsample_computes_interval(client: TestClient) -> None:
    headers = _login(client)
    response = client.get(
        "/api/v1/history/downsample",
        params={
            "variable": "VOLTAGE_A",
            "from": "2026-01-01T00:00:00Z",
            "to": "2026-07-01T00:00:00Z",
            "target_points": 100,
        },
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["interval_seconds"] > 900  # rango de 6 meses / 100 puntos >> 15min


def test_history_range_instant_returns_stats(
    client: TestClient, fake_influx_repo: FakeInfluxRepository
) -> None:
    headers = _login(client)
    fake_influx_repo.instant_reduce_value = 42.0
    response = client.get(
        "/api/v1/history/range",
        params={"variable": "VOLTAGE_A", "from": FROM, "to": TO},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["mean"] == 42.0
    assert body["total_kwh"] is None


def test_history_range_counter_returns_total_only(
    client: TestClient, fake_influx_repo: FakeInfluxRepository
) -> None:
    headers = _login(client)
    fake_influx_repo.energy_total_value = 12.3
    response = client.get(
        "/api/v1/history/range",
        params={"variable": "POWER_ACTIVE_TOTAL_NEG", "from": FROM, "to": TO},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["total_kwh"] == 12.3
    assert body["mean"] is None
