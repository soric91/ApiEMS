import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeInfluxRepository


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"username": "testuser", "password": "testpass"}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("prefix", ["consumption", "export"])
@pytest.mark.parametrize("period", ["day", "week", "month", "year"])
def test_energy_endpoints_require_auth(client: TestClient, prefix: str, period: str) -> None:
    assert client.get(f"/api/v1/{prefix}/{period}").status_code == 401


@pytest.mark.parametrize("prefix", ["consumption", "export"])
@pytest.mark.parametrize("period", ["day", "week", "month", "year"])
def test_energy_endpoints_return_summary(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, prefix: str, period: str
) -> None:
    headers = _login(client)
    fake_influx_repo.energy_total_value = 7.7
    response = client.get(f"/api/v1/{prefix}/{period}", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["period"] == period
    assert "series" in body
    if period == "year":
        # /year suma un energy_total() por cada mes calendario transcurrido
        assert body["total_kwh"] == pytest.approx(7.7 * len(body["series"]))
    else:
        assert body["total_kwh"] == 7.7


def test_consumption_uses_import_counter(
    client: TestClient, fake_influx_repo: FakeInfluxRepository
) -> None:
    headers = _login(client)
    client.get("/api/v1/consumption/day", headers=headers)
    called_counter = fake_influx_repo.calls[-1][1]
    assert called_counter == "POWER_ACTIVE_TOTAL_POS"


def test_export_uses_export_counter(
    client: TestClient, fake_influx_repo: FakeInfluxRepository
) -> None:
    headers = _login(client)
    client.get("/api/v1/export/day", headers=headers)
    called_counter = fake_influx_repo.calls[-1][1]
    assert called_counter == "POWER_ACTIVE_TOTAL_NEG"


def test_year_summary_gathers_monthly_totals(
    client: TestClient, fake_influx_repo: FakeInfluxRepository
) -> None:
    headers = _login(client)
    fake_influx_repo.energy_total_value = 1.0
    response = client.get("/api/v1/consumption/year", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    # al menos un mes ya transcurrido este año -> al menos 1 punto
    assert len(body["series"]) >= 1
    assert body["total_kwh"] == len(body["series"]) * 1.0
