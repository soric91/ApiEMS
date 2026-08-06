"""CrmClient contra un httpx.MockTransport — sin red real."""

import json
import time

import httpx
import pytest

from app.core.config import Settings
from app.services.crm.client import CrmClient, CrmClientError


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        CRM_BASE_URL="http://crm.test",
        CRM_CLIENT_ID="svc_test",
        CRM_CLIENT_SECRET="secret123",
    )


async def test_get_tariffs_exchanges_credential_then_fetches() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/service/token"):
            body = json.loads(request.content)
            assert body == {"client_id": "svc_test", "client_secret": "secret123"}
            return httpx.Response(
                200,
                json={
                    "access_token": "tok-1",
                    "token_type": "bearer",
                    "expires_in": 900,
                    "permisos": ["tariffs:read"],
                    "scope_client_id": None,
                },
            )
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

    assert calls == ["/api/v1/service/token", "/api/v1/tariffs"]
    assert tariffs[0]["valor_importado"] == "902.28"


async def test_get_tariffs_reuses_cached_token_while_fresh() -> None:
    token_requests = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/service/token"):
            token_requests["count"] += 1
            return httpx.Response(
                200, json={"access_token": "tok-1", "expires_in": 900, "permisos": []}
            )
        return httpx.Response(200, json={"items": [], "total": 0, "limit": 200, "offset": 0})

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    await client.get_tariffs()
    await client.get_tariffs()

    assert token_requests["count"] == 1


async def test_get_tariffs_renews_token_before_expiry_without_a_401() -> None:
    """Un token a punto de vencer se renueva proactivamente — no hace falta
    que el servidor lo rechace primero."""
    token_requests = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/service/token"):
            token_requests["count"] += 1
            return httpx.Response(
                200, json={"access_token": "tok-new", "expires_in": 900, "permisos": []}
            )
        assert request.headers["authorization"] == "Bearer tok-new"
        return httpx.Response(200, json={"items": [], "total": 0, "limit": 200, "offset": 0})

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    client._token = "tok-about-to-expire"  # pyright: ignore[reportPrivateUsage]
    client._token_expires_at = time.monotonic() - 1  # pyright: ignore[reportPrivateUsage]

    await client.get_tariffs()

    assert token_requests["count"] == 1


async def test_client_secret_never_appears_in_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid credential"})

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(CrmClientError) as exc_info:
        await client.get_tariffs()

    assert "secret123" not in str(exc_info.value)


async def test_get_tariffs_relogs_in_on_401() -> None:
    token_requests = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/service/token"):
            token_requests["count"] += 1
            return httpx.Response(
                200, json={"access_token": "tok-fresh", "expires_in": 900, "permisos": []}
            )
        if request.headers["authorization"] == "Bearer tok-stale":
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json={"items": [], "total": 0, "limit": 200, "offset": 0})

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    client._token = "tok-stale"  # pyright: ignore[reportPrivateUsage]
    client._token_expires_at = time.monotonic() + 999  # pyright: ignore[reportPrivateUsage]

    tariffs = await client.get_tariffs()

    assert tariffs == []
    assert token_requests["count"] == 1


async def test_get_tariffs_raises_on_persistent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/service/token"):
            return httpx.Response(
                200, json={"access_token": "tok-1", "expires_in": 900, "permisos": []}
            )
        return httpx.Response(500, json={"detail": "boom"})

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(CrmClientError):
        await client.get_tariffs()


async def test_login_raises_when_not_configured() -> None:
    client = CrmClient(Settings(_env_file=None))  # pyright: ignore[reportCallIssue]
    assert client.configured is False
    with pytest.raises(CrmClientError):
        await client.get_tariffs()
