from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.fakes import FakeInfluxRepository


@pytest.fixture(autouse=True)
def tariff_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Aísla estos tests de `data/tariffs.json` real del proyecto."""
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


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly", "yearly"])
def test_report_endpoints_require_auth(client: TestClient, period: str) -> None:
    assert client.get(f"/api/v1/reports/{period}").status_code == 401


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly", "yearly"])
def test_report_endpoints_return_data(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, period: str
) -> None:
    headers = _login(client)
    fake_influx_repo.energy_total_value = 6.0
    response = client.get(f"/api/v1/reports/{period}", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["report_type"] == period
    assert body["consumption_kwh"] == 6.0
    assert body["net_balance_kwh"] == 0.0
    assert "kpis" in body
    assert "max_demand" in body
    assert "costs" in body


def test_report_custom_requires_bounds(client: TestClient) -> None:
    headers = _login(client)
    response = client.get("/api/v1/reports/custom", headers=headers)
    assert response.status_code == 422  # from/to son requeridos


def test_report_custom_invalid_range_rejected(client: TestClient) -> None:
    headers = _login(client)
    response = client.get(
        "/api/v1/reports/custom",
        params={"from": "2026-07-02T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 400


def test_report_custom_ok(client: TestClient, fake_influx_repo: FakeInfluxRepository) -> None:
    headers = _login(client)
    fake_influx_repo.energy_total_value = 2.0
    response = client.get(
        "/api/v1/reports/custom",
        params={"from": "2026-07-01T00:00:00Z", "to": "2026-07-02T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["report_type"] == "custom"
