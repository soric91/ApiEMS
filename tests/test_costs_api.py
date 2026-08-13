"""Tests HTTP de /costs/range.

Los costos de periodos fijos (/costs/{day,week,month,year}) ya no existen
(fase V3): el contrato vigente es /reports/{daily,weekly,monthly,yearly} y
el rango libre /costs/range, que es lo que queda acá.
"""

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies.tariff import get_tariff_config
from app.models.variables import Variable
from app.schemas.influx import EnergyPoint
from app.schemas.tariff import TariffConfig, TariffPeriod
from tests.fakes import FakeInfluxRepository


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
