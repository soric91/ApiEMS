from fastapi.testclient import TestClient


def test_health_returns_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {"status": "ok"}


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert "message" in body


def test_timing_headers_present(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert "X-Process-Time" in response.headers
    assert "X-Request-ID" in response.headers
