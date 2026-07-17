from fastapi.testclient import TestClient

from tests.fakes import FakeInfluxRepository


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"username": "testuser", "password": "testpass"}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_kpis_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/kpis").status_code == 401


def test_kpis_default_period(client: TestClient, fake_influx_repo: FakeInfluxRepository) -> None:
    headers = _login(client)
    fake_influx_repo.energy_total_value = 1.5
    response = client.get("/api/v1/kpis", headers=headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["consumption_daily_kwh"] == 1.5
    assert body["export_monthly_kwh"] == 1.5


def test_kpis_invalid_range_rejected(client: TestClient) -> None:
    headers = _login(client)
    response = client.get(
        "/api/v1/kpis",
        params={"from": "2026-07-02T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 400
