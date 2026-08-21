from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeInfluxRepository


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly", "yearly"])
def test_report_endpoints_require_auth(client: TestClient, period: str) -> None:
    assert client.get(f"/api/v1/reports/{period}").status_code == 401


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly", "yearly"])
def test_report_endpoints_return_data(
    client: TestClient,
    fake_influx_repo: FakeInfluxRepository,
    period: str,
    auth_headers: dict[str, str],
) -> None:
    fake_influx_repo.energy_total_value = 6.0
    response = client.get(f"/api/v1/reports/{period}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["report_type"] == period
    assert body["consumption_kwh"] == 6.0
    assert body["net_balance_kwh"] == 0.0
    assert "kpis" in body
    assert "max_demand" in body
    assert "costs" in body


def test_report_custom_requires_bounds(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/reports/custom", headers=auth_headers)
    assert response.status_code == 422  # from/to son requeridos


def test_report_custom_invalid_range_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/reports/custom",
        params={"from": "2026-07-02T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_report_custom_ok(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    fake_influx_repo.energy_total_value = 2.0
    response = client.get(
        "/api/v1/reports/custom",
        params={"from": "2026-07-01T00:00:00Z", "to": "2026-07-02T00:00:00Z"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["report_type"] == "custom"


def test_report_daily_reads_power_series_once(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    """F2.2 + F0.1: KPIs + demanda + factor de carga + carga base comparten las
    series de potencia. Son DOS lecturas y solo dos: una `mean` (promedios) y
    una `max` (la demanda pico, que el promedio borraba). Ni una por indicador,
    ni la misma agregación dos veces."""
    response = client.get("/api/v1/reports/daily", headers=auth_headers)
    assert response.status_code == 200

    power_reads = [
        call for call in fake_influx_repo.calls if call[0] == "instant_series" and call[1] == "TotW"
    ]
    aggregations = [call[2] for call in power_reads]
    assert sorted(aggregations) == ["max", "mean"]


@pytest.mark.parametrize(
    ("bucket", "esperado"),
    [("hour", timedelta(hours=1)), ("day", timedelta(days=1)), ("week", timedelta(days=7))],
)
def test_report_custom_agrupa_las_barras_como_se_le_pida(
    client: TestClient,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
    bucket: str,
    esperado: timedelta,
) -> None:
    response = client.get(
        "/api/v1/reports/custom",
        params={
            "from": "2026-07-21T05:00:00Z",
            "to": "2026-08-20T05:00:00Z",
            "bucket": bucket,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert set(fake_influx_repo.energy_series_every) == {esperado}


def test_report_custom_sin_bucket_agrupa_por_dia(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    # 30 días sin pedir nada: barras diarias, no las ~500 que salían de la
    # ventana de lectura.
    response = client.get(
        "/api/v1/reports/custom",
        params={"from": "2026-07-21T05:00:00Z", "to": "2026-08-20T05:00:00Z"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert set(fake_influx_repo.energy_series_every) == {timedelta(days=1)}


def test_report_bucket_invalido_se_rechaza(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/reports/custom",
        params={"from": "2026-07-21T05:00:00Z", "to": "2026-08-20T05:00:00Z", "bucket": "minuto"},
        headers=auth_headers,
    )

    assert response.status_code == 422
