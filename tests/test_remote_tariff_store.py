"""RemoteTariffStore: degradación cuando CRMBackend no responde."""

import httpx
import pytest

from app.core.config import Settings
from app.services.crm.client import CrmClient
from app.services.tariff.store import RemoteTariffStore


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        CRM_BASE_URL="http://crm.test",
        CRM_CLIENT_ID="svc_test",
        CRM_CLIENT_SECRET="secret123",
    )


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 900, "permisos": []})


def _tariffs_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "items": [
                {"mes": "2026-06-01", "valor_importado": "902.28", "valor_excedente": "114.34"}
            ],
            "total": 1,
        },
    )


async def test_load_returns_live_data_when_crm_is_up() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/service/token"):
            return _token_response()
        return _tariffs_response()

    store = RemoteTariffStore(CrmClient(_settings(), transport=httpx.MockTransport(handler)))
    config = await store.load()

    assert len(config.periods) == 1
    assert config.periods[0].cu_cop_kwh == 902.28


async def test_load_degrades_to_last_good_when_crm_fails() -> None:
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/service/token"):
            return _token_response()
        if state["fail"]:
            return httpx.Response(500, json={"detail": "boom"})
        return _tariffs_response()

    crm = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    store = RemoteTariffStore(crm)

    good = await store.load()
    state["fail"] = True
    degraded = await store.load()

    assert degraded == good
    assert len(degraded.periods) == 1


async def test_load_returns_empty_when_crm_fails_and_never_cached() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    store = RemoteTariffStore(CrmClient(_settings(), transport=httpx.MockTransport(handler)))
    config = await store.load()

    assert config.periods == []


async def test_load_never_raises() -> None:
    """El motor de costos que consume esto no debe recibir un 500 nunca —
    CrmClientError se convierte siempre en degradación, no se propaga."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    store = RemoteTariffStore(CrmClient(_settings(), transport=httpx.MockTransport(handler)))
    try:
        await store.load()
    except Exception as exc:
        pytest.fail(f"RemoteTariffStore.load() no debe propagar excepciones, propagó {exc!r}")
