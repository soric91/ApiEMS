"""Tests HTTP de /costs/{day,week,month,year}."""

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies.tariff import get_tariff_config
from app.models.variables import Variable
from app.schemas.influx import EnergyPoint
from app.schemas.tariff import TariffConfig, TariffPeriod
from tests.fakes import FakeInfluxRepository


def _seed_current_month_tariff(app: FastAPI) -> str:
    now = datetime.now(tz=UTC)
    month = f"{now.year:04d}-{now.month:02d}"
    config = TariffConfig(
        periods=[TariffPeriod(month=month, cu_cop_kwh=859.19, excedente_cop_kwh=114.34)],
    )
    app.dependency_overrides[get_tariff_config] = lambda: config
    return month


def test_costs_requires_auth(client: TestClient) -> None:
    for period in ["day", "week", "month", "year"]:
        assert client.get(f"/api/v1/costs/{period}").status_code == 401


def test_costs_month_without_tariff_returns_zero_and_flags_stale(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/costs/month", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["consumption_cost_cop"] == 0.0
    assert body["net_cost_cop"] == 0.0
    assert len(body["stale_months"]) >= 1


def test_costs_day_computes_from_series(
    app: FastAPI,
    client: TestClient,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    month = _seed_current_month_tariff(app)
    now = datetime.now(tz=UTC)
    fake_influx_repo.energy_series_points_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [EnergyPoint(time=now, value=10.0)],
        Variable.POWER_ACTIVE_TOTAL_NEG: [EnergyPoint(time=now, value=2.0)],
    }

    response = client.get("/api/v1/costs/day", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["consumption_cost_cop"] == round(10.0 * 859.19, 2)
    # Importado (10) > exportado (2) el mismo mes: todo tramo 1, al precio
    # de importación, no al de excedente.
    assert body["export_credit_cop"] == round(2.0 * 859.19, 2)
    assert body["months_used"] == [month]


def test_costs_export_beyond_import_uses_tier2_rate(
    app: FastAPI,
    client: TestClient,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    _seed_current_month_tariff(app)
    now = datetime.now(tz=UTC)
    fake_influx_repo.energy_series_points_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [EnergyPoint(time=now, value=5.0)],
        Variable.POWER_ACTIVE_TOTAL_NEG: [EnergyPoint(time=now, value=20.0)],
    }

    response = client.get("/api/v1/costs/month", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()["data"]
    # 5 kWh (tramo 1, precio importación) + 15 kWh (tramo 2, precio excedente)
    expected_credit = round(5.0 * 859.19 + 15.0 * 114.34, 2)
    assert body["export_credit_cop"] == expected_credit
    assert body["net_cost_cop"] == round(5.0 * 859.19 - expected_credit, 2)


def test_costs_year_shape(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/costs/year", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["period"] == "year"
    assert "months_used" in body


def test_costs_range_requires_auth(client: TestClient) -> None:
    response = client.get(
        "/api/v1/costs/range",
        params={"from": "2026-01-01T00:00:00Z", "to": "2026-01-31T00:00:00Z"},
    )
    assert response.status_code == 401


def test_costs_range_rejects_inverted_bounds(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/costs/range",
        params={"from": "2026-01-31T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_costs_range_computes_series(
    app: FastAPI,
    client: TestClient,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    config = TariffConfig(
        periods=[TariffPeriod(month="2026-01", cu_cop_kwh=859.19, excedente_cop_kwh=114.34)],
    )
    app.dependency_overrides[get_tariff_config] = lambda: config
    point_time = datetime(2026, 1, 15, tzinfo=UTC)
    fake_influx_repo.energy_series_points_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [EnergyPoint(time=point_time, value=10.0)],
        Variable.POWER_ACTIVE_TOTAL_NEG: [],
    }
    fake_influx_repo.energy_total_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: 10.0,
        Variable.POWER_ACTIVE_TOTAL_NEG: 0.0,
    }

    response = client.get(
        "/api/v1/costs/range",
        params={"from": "2026-01-01T00:00:00Z", "to": "2026-01-31T00:00:00Z"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["period"] == "custom"
    assert body["consumption_cost_cop"] == round(10.0 * 859.19, 2)
    assert len(body["series"]) == 1
    assert body["series"][0]["consumption_kwh"] == 10.0
