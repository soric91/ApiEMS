"""GET /dashboard/summary: payload consolidado del panel en una llamada.

La clave del endpoint no es inventar datos sino servir en un solo request lo
que hoy llega en tres (/dashboard, /costs/range, /kpis) — por eso los tests
centrales son de consistencia: los valores del resumen tienen que coincidir
con los de los endpoints individuales del mismo período.
"""

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.mqtt import DeviceReading
from app.services.realtime.state import RealtimeState
from app.utils.period import start_of_day
from tests.fakes import FakeInfluxRepository

_DEVICE_ID = "bf6a469f-4c2a-4402-9438-49a491ad2238"

READING = DeviceReading(
    device_name="Modbus_DTSU666_11",
    device_id=11,
    identify_device=_DEVICE_ID,
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


def _seed(client: TestClient, app: FastAPI, repo: FakeInfluxRepository, value: float) -> None:
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    repo.energy_total_value = value


def test_dashboard_summary_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/dashboard/summary").status_code == 401


def test_dashboard_summary_503_without_realtime_data(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/dashboard/summary", headers=auth_headers)
    assert response.status_code == 503


def test_dashboard_summary_full_payload(
    client: TestClient,
    app: FastAPI,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    _seed(client, app, fake_influx_repo, 3.2)
    response = client.get("/api/v1/dashboard/summary", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()["data"]

    # En vivo (RAM)
    assert data["device_id"] == _DEVICE_ID
    assert data["power_active_total_w"] == -442.2
    assert data["voltage_a"] == 120.4
    assert data["voltage_b"] == 121.2
    assert data["current_a"] == 1.93
    assert data["current_b"] == 2.81
    assert data["power_factor"] == 0.75

    # Energía de hoy y del mes
    assert data["consumption_today_kwh"] == 3.2
    assert data["consumption_month_kwh"] == 3.2
    assert data["export_today_kwh"] == 3.2
    assert data["export_month_kwh"] == 3.2

    # Costos día/mes y KPIs del día
    assert data["costs_day"]["consumption_kwh"] == 3.2
    assert data["costs_month"]["consumption_kwh"] == 3.2
    assert data["kpis"]["consumption_daily_kwh"] == 3.2


def test_dashboard_summary_consistent_with_dashboard(
    client: TestClient,
    app: FastAPI,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    """Los campos que comparte con /dashboard tienen que ser idénticos."""
    _seed(client, app, fake_influx_repo, 4.0)

    summary = client.get("/api/v1/dashboard/summary", headers=auth_headers).json()["data"]
    dashboard = client.get("/api/v1/dashboard", headers=auth_headers).json()["data"]

    for field in (
        "power_active_total_w",
        "voltage_a",
        "voltage_b",
        "current_a",
        "current_b",
        "power_factor",
        "consumption_today_kwh",
        "consumption_month_kwh",
        "export_today_kwh",
        "export_month_kwh",
    ):
        assert summary[field] == dashboard[field], f"campo {field} divergió de /dashboard"


def test_dashboard_summary_costs_day_consistent_with_costs_range(
    client: TestClient,
    app: FastAPI,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    """costs_day tiene que coincidir con /costs/range del día en curso."""
    _seed(client, app, fake_influx_repo, 5.5)

    summary_costs = client.get("/api/v1/dashboard/summary", headers=auth_headers).json()["data"][
        "costs_day"
    ]
    from_ = start_of_day("America/Bogota")
    to = datetime.now(tz=UTC)
    range_costs = client.get(
        "/api/v1/costs/range",
        params={"from": from_.isoformat(), "to": to.isoformat()},
        headers=auth_headers,
    ).json()["data"]

    for field in (
        "consumption_kwh",
        "export_kwh",
        "consumption_cost_cop",
        "export_credit_cop",
        "net_cost_cop",
        "stale_months",
    ):
        assert summary_costs[field] == range_costs[field], f"costos: campo {field} divergió"


def test_dashboard_summary_kpis_consistent_with_kpis_endpoint(
    client: TestClient,
    app: FastAPI,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    """Los KPIs del resumen tienen que coincidir con /kpis (mismo período "hoy")."""
    _seed(client, app, fake_influx_repo, 2.0)

    summary_kpis = client.get("/api/v1/dashboard/summary", headers=auth_headers).json()["data"][
        "kpis"
    ]
    endpoint_kpis = client.get("/api/v1/kpis", headers=auth_headers).json()["data"]

    for field in (
        "power_avg_w",
        "power_max_w",
        "voltage_avg_v",
        "voltage_min_v",
        "voltage_max_v",
        "current_avg_a",
        "power_factor_avg",
        "consumption_daily_kwh",
        "consumption_weekly_kwh",
        "consumption_monthly_kwh",
        "export_daily_kwh",
        "export_monthly_kwh",
    ):
        assert summary_kpis[field] == endpoint_kpis[field], f"kpis: campo {field} divergió"


def test_dashboard_summary_unknown_device_id_404(
    client: TestClient, app: FastAPI, auth_headers: dict[str, str]
) -> None:
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    response = client.get(
        "/api/v1/dashboard/summary", params={"device_id": "99"}, headers=auth_headers
    )
    assert response.status_code == 404
