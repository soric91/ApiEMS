"""CrmClient contra un httpx.MockTransport — sin red real."""

import httpx
import pytest

from app.core.config import Settings
from app.services.crm.client import CrmClient, CrmClientError


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        CRM_BASE_URL="http://crm.test",
        CRM_SERVICE_EMAIL="svc@example.com",
        CRM_SERVICE_PASSWORD="secret123",
    )


async def test_get_tariffs_logs_in_then_fetches() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "tok-1", "refresh_token": "r"})
        assert request.headers["authorization"] == "Bearer tok-1"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"mes": "2026-06-01", "valor_importado": "902.28", "valor_excedente": "114.34"}
                ],
                "total": 1,
                "limit": 200,
                "offset": 0,
            },
        )

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    tariffs = await client.get_tariffs()

    assert calls == ["/api/v1/auth/login", "/api/v1/tariffs"]
    assert tariffs[0]["valor_importado"] == "902.28"


async def test_get_tariffs_relogs_in_on_401() -> None:
    logins = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            logins["count"] += 1
            return httpx.Response(200, json={"access_token": "tok-fresh"})
        if request.headers["authorization"] == "Bearer tok-stale":
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json={"items": [], "total": 0, "limit": 200, "offset": 0})

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    client._token = "tok-stale"  # pyright: ignore[reportPrivateUsage]

    tariffs = await client.get_tariffs()

    assert tariffs == []
    assert logins["count"] == 1


async def test_get_tariffs_raises_on_persistent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "tok-1"})
        return httpx.Response(500, json={"detail": "boom"})

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(CrmClientError):
        await client.get_tariffs()


async def test_login_raises_when_not_configured() -> None:
    client = CrmClient(Settings(_env_file=None))  # pyright: ignore[reportCallIssue]
    assert client.configured is False
    with pytest.raises(CrmClientError):
        await client.get_tariffs()
