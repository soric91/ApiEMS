"""Tests HTTP de /costs/{day,week,month,year}."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.variables import Variable
from app.schemas.influx import EnergyPoint
from app.schemas.tariff import TariffConfig, TariffPeriod
from app.services.tariff.store import save_tariff_config
from tests.fakes import FakeInfluxRepository


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


async def _seed_current_month_tariff(path: Path) -> str:
    now = datetime.now(tz=UTC)
    month = f"{now.year:04d}-{now.month:02d}"
    config = TariffConfig(
        periods=[TariffPeriod(month=month, cu_cop_kwh=859.19, excedente_cop_kwh=114.34)],
    )
    await save_tariff_config(str(path), config)
    return month


@pytest.mark.parametrize("period", ["day", "week", "month", "year"])
def test_costs_requires_auth(client: TestClient, tariff_path: Path, period: str) -> None:
    assert client.get(f"/api/v1/costs/{period}").status_code == 401


def test_costs_month_without_tariff_returns_zero_and_flags_stale(
    client: TestClient, tariff_path: Path
) -> None:
    headers = _login(client)
    response = client.get("/api/v1/costs/month", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["consumption_cost_cop"] == 0.0
    assert body["net_cost_cop"] == 0.0
    assert len(body["stale_months"]) >= 1


async def test_costs_day_computes_from_series(
    client: TestClient, tariff_path: Path, fake_influx_repo: FakeInfluxRepository
) -> None:
    month = await _seed_current_month_tariff(tariff_path)
    now = datetime.now(tz=UTC)
    fake_influx_repo.energy_series_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [EnergyPoint(time=now, value=10.0)],
        Variable.POWER_ACTIVE_TOTAL_NEG: [EnergyPoint(time=now, value=2.0)],
    }

    headers = _login(client)
    response = client.get("/api/v1/costs/day", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["consumption_cost_cop"] == round(10.0 * 859.19, 2)
    # Importado (10) > exportado (2) el mismo mes: todo tramo 1, al precio
    # de importación, no al de excedente.
    assert body["export_credit_cop"] == round(2.0 * 859.19, 2)
    assert body["months_used"] == [month]


async def test_costs_export_beyond_import_uses_tier2_rate(
    client: TestClient, tariff_path: Path, fake_influx_repo: FakeInfluxRepository
) -> None:
    await _seed_current_month_tariff(tariff_path)
    now = datetime.now(tz=UTC)
    fake_influx_repo.energy_series_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [EnergyPoint(time=now, value=5.0)],
        Variable.POWER_ACTIVE_TOTAL_NEG: [EnergyPoint(time=now, value=20.0)],
    }

    headers = _login(client)
    response = client.get("/api/v1/costs/month", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    # 5 kWh (tramo 1, precio importación) + 15 kWh (tramo 2, precio excedente)
    expected_credit = round(5.0 * 859.19 + 15.0 * 114.34, 2)
    assert body["export_credit_cop"] == expected_credit
    assert body["net_cost_cop"] == round(5.0 * 859.19 - expected_credit, 2)


def test_costs_year_shape(client: TestClient, tariff_path: Path) -> None:
    headers = _login(client)
    response = client.get("/api/v1/costs/year", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["period"] == "year"
    assert "months_used" in body


def test_costs_range_requires_auth(client: TestClient, tariff_path: Path) -> None:
    response = client.get(
        "/api/v1/costs/range",
        params={"from": "2026-01-01T00:00:00Z", "to": "2026-01-31T00:00:00Z"},
    )
    assert response.status_code == 401


def test_costs_range_rejects_inverted_bounds(client: TestClient, tariff_path: Path) -> None:
    headers = _login(client)
    response = client.get(
        "/api/v1/costs/range",
        params={"from": "2026-01-31T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 400


async def test_costs_range_computes_series(
    client: TestClient, tariff_path: Path, fake_influx_repo: FakeInfluxRepository
) -> None:
    config = TariffConfig(
        periods=[TariffPeriod(month="2026-01", cu_cop_kwh=859.19, excedente_cop_kwh=114.34)],
    )
    await save_tariff_config(str(tariff_path), config)
    point_time = datetime(2026, 1, 15, tzinfo=UTC)
    fake_influx_repo.energy_series_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [EnergyPoint(time=point_time, value=10.0)],
        Variable.POWER_ACTIVE_TOTAL_NEG: [],
    }
    fake_influx_repo.energy_total_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: 10.0,
        Variable.POWER_ACTIVE_TOTAL_NEG: 0.0,
    }

    headers = _login(client)
    response = client.get(
        "/api/v1/costs/range",
        params={"from": "2026-01-01T00:00:00Z", "to": "2026-01-31T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["period"] == "custom"
    assert body["consumption_cost_cop"] == round(10.0 * 859.19, 2)
    assert len(body["series"]) == 1
    assert body["series"][0]["consumption_kwh"] == 10.0
