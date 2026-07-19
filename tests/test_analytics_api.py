from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.fakes import FakeInfluxRepository

ENDPOINTS = [
    "/api/v1/analytics",
    "/api/v1/analytics/daily-profile",
    "/api/v1/analytics/monthly-profile",
    "/api/v1/analytics/max-demand",
    "/api/v1/analytics/load-factor",
    "/api/v1/analytics/base-load",
]


@pytest.fixture
def tariff_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tariffs.json"
    monkeypatch.setenv("TARIFF_CONFIG_PATH", str(path))
    get_settings.cache_clear()
    return path


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"username": "testuser", "password": "testpass"}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_all_analytics_endpoints_require_auth(client: TestClient) -> None:
    for endpoint in ENDPOINTS:
        assert client.get(endpoint).status_code == 401
    assert (
        client.get(
            "/api/v1/analytics/compare",
            params={
                "from_a": "2026-01-01T00:00:00Z",
                "to_a": "2026-01-02T00:00:00Z",
                "from_b": "2026-01-08T00:00:00Z",
                "to_b": "2026-01-09T00:00:00Z",
            },
        ).status_code
        == 401
    )
    assert client.get("/api/v1/analytics/summary").status_code == 401


def test_all_analytics_endpoints_default_to_today(client: TestClient) -> None:
    headers = _login(client)
    for endpoint in ENDPOINTS:
        response = client.get(endpoint, headers=headers)
        assert response.status_code == 200, endpoint
        assert response.json()["success"] is True


def test_analytics_overview_shape(
    client: TestClient, fake_influx_repo: FakeInfluxRepository
) -> None:
    headers = _login(client)
    fake_influx_repo.energy_total_value = 2.5
    response = client.get("/api/v1/analytics", headers=headers)
    body = response.json()["data"]
    assert body["consumption_kwh"] == 2.5
    assert "max_demand" in body
    assert "load_factor" in body
    assert "base_load" in body


def test_analytics_invalid_range_rejected(client: TestClient) -> None:
    headers = _login(client)
    response = client.get(
        "/api/v1/analytics/max-demand",
        params={"from": "2026-07-02T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 400


def test_monthly_profile_invalid_range_rejected(client: TestClient) -> None:
    headers = _login(client)
    response = client.get(
        "/api/v1/analytics/monthly-profile",
        params={"from": "2026-07-02T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 400


def test_base_load_percentile_bounds(client: TestClient) -> None:
    headers = _login(client)
    ok = client.get("/api/v1/analytics/base-load", params={"percentile": 0.2}, headers=headers)
    assert ok.status_code == 200
    bad = client.get("/api/v1/analytics/base-load", params={"percentile": 1.5}, headers=headers)
    assert bad.status_code == 422


def test_compare_requires_all_bounds(client: TestClient) -> None:
    headers = _login(client)
    response = client.get(
        "/api/v1/analytics/compare",
        params={
            "from_a": "2026-01-01T00:00:00Z",
            "to_a": "2026-01-02T00:00:00Z",
            "from_b": "2026-01-08T00:00:00Z",
            "to_b": "2026-01-09T00:00:00Z",
        },
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert "period_a" in body and "period_b" in body


def test_analytics_summary_shape(
    client: TestClient, tariff_path: Path, fake_influx_repo: FakeInfluxRepository
) -> None:
    headers = _login(client)
    fake_influx_repo.energy_total_value = 3.0
    response = client.get("/api/v1/analytics/summary", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["consumption_daily_kwh"] == 3.0
    assert "hourly_profile" in body
    assert "peak_consumption_hour" in body
    assert "peak_export_hour" in body
    assert body["efficiency"] is None  # sin tarifa configurada


def test_analytics_summary_invalid_range_rejected(client: TestClient, tariff_path: Path) -> None:
    headers = _login(client)
    response = client.get(
        "/api/v1/analytics/summary",
        params={"from": "2026-07-02T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 400


def test_compare_invalid_range_rejected(client: TestClient) -> None:
    headers = _login(client)
    response = client.get(
        "/api/v1/analytics/compare",
        params={
            "from_a": "2026-01-02T00:00:00Z",
            "to_a": "2026-01-01T00:00:00Z",
            "from_b": "2026-01-08T00:00:00Z",
            "to_b": "2026-01-09T00:00:00Z",
        },
        headers=headers,
    )
    assert response.status_code == 400
