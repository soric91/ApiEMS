from fastapi.testclient import TestClient

from tests.fakes import FakeInfluxRepository

ENDPOINTS = [
    "/api/v1/analytics/daily-profile",
    "/api/v1/analytics/monthly-profile",
]


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
    assert client.get("/api/v1/analytics/reactive-quadrants").status_code == 401


def test_all_analytics_endpoints_default_to_today(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    for endpoint in ENDPOINTS:
        response = client.get(endpoint, headers=auth_headers)
        assert response.status_code == 200, endpoint
        assert response.json()["success"] is True


def test_monthly_profile_invalid_range_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/analytics/monthly-profile",
        params={"from": "2026-07-02T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_compare_requires_all_bounds(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/analytics/compare",
        params={
            "from_a": "2026-01-01T00:00:00Z",
            "to_a": "2026-01-02T00:00:00Z",
            "from_b": "2026-01-08T00:00:00Z",
            "to_b": "2026-01-09T00:00:00Z",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert "period_a" in body and "period_b" in body


def test_analytics_summary_shape(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    fake_influx_repo.energy_total_value = 3.0
    response = client.get("/api/v1/analytics/summary", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["consumption_daily_kwh"] == 3.0
    assert "hourly_profile" in body
    assert "peak_consumption_hour" in body
    assert "peak_export_hour" in body
    assert body["efficiency"] is None  # sin tarifa configurada


def test_analytics_summary_invalid_range_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/analytics/summary",
        params={"from": "2026-07-02T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_compare_invalid_range_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/analytics/compare",
        params={
            "from_a": "2026-01-02T00:00:00Z",
            "to_a": "2026-01-01T00:00:00Z",
            "from_b": "2026-01-08T00:00:00Z",
            "to_b": "2026-01-09T00:00:00Z",
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
