"""Tests HTTP de GET/PUT /tariff — TARIFF_CONFIG_PATH aislado por test
(apunta a un archivo temporal, nunca al data/tariffs.json real)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings


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


def test_tariff_requires_auth(client: TestClient, tariff_path: Path) -> None:
    assert client.get("/api/v1/tariff").status_code == 401
    assert client.put("/api/v1/tariff", json={}).status_code == 401


def test_get_tariff_empty_when_no_file(client: TestClient, tariff_path: Path) -> None:
    headers = _login(client)
    response = client.get("/api/v1/tariff", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["periods"] == []


def test_put_then_get_roundtrip(client: TestClient, tariff_path: Path) -> None:
    headers = _login(client)
    payload = {
        "umbral_cs_kwh": 130.0,
        "periods": [
            {"month": "2026-01", "cu_cop_kwh": 859.19, "excedente_cop_kwh": 114.34}
        ],
    }
    put_response = client.put("/api/v1/tariff", json=payload, headers=headers)
    assert put_response.status_code == 200

    get_response = client.get("/api/v1/tariff", headers=headers)
    assert get_response.json()["data"] == payload

    # persistió en disco de verdad, no solo en memoria
    assert tariff_path.exists()


def test_put_invalid_month_format_rejected(client: TestClient, tariff_path: Path) -> None:
    headers = _login(client)
    payload = {
        "periods": [
            {"month": "2026-13", "cu_cop_kwh": 100.0, "excedente_cop_kwh": 10.0}
        ],
    }
    response = client.put("/api/v1/tariff", json=payload, headers=headers)
    assert response.status_code == 422


def test_put_replaces_full_config_not_merge(client: TestClient, tariff_path: Path) -> None:
    headers = _login(client)
    first = {
        "periods": [
            {"month": "2026-01", "cu_cop_kwh": 800.0, "excedente_cop_kwh": 100.0}
        ],
    }
    client.put("/api/v1/tariff", json=first, headers=headers)

    second = {
        "periods": [
            {"month": "2026-02", "cu_cop_kwh": 801.24, "excedente_cop_kwh": 120.0}
        ],
    }
    client.put("/api/v1/tariff", json=second, headers=headers)

    get_response = client.get("/api/v1/tariff", headers=headers)
    body = get_response.json()["data"]
    assert len(body["periods"]) == 1
    assert body["periods"][0]["month"] == "2026-02"
