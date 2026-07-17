import pytest

from app.core.config import Settings
from app.services.influx.client import InfluxService


def _settings() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


async def test_ping_false_before_connect() -> None:
    service = InfluxService(_settings())
    assert await service.ping() is False


async def test_query_api_raises_before_connect() -> None:
    service = InfluxService(_settings())
    with pytest.raises(RuntimeError, match="no conectado"):
        _ = service.query_api


class _FakeClient:
    def __init__(self, ping_result: bool = True, raise_on_ping: bool = False) -> None:
        self._ping_result = ping_result
        self._raise = raise_on_ping

    async def ping(self) -> bool:
        if self._raise:
            raise ConnectionError("influx unreachable")
        return self._ping_result

    def query_api(self) -> str:
        return "fake-query-api"


async def test_ping_true_when_client_reachable() -> None:
    service = InfluxService(_settings())
    service._client = _FakeClient(ping_result=True)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    assert await service.ping() is True


async def test_ping_false_on_exception() -> None:
    service = InfluxService(_settings())
    service._client = _FakeClient(raise_on_ping=True)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    assert await service.ping() is False


async def test_query_api_delegates_to_client() -> None:
    service = InfluxService(_settings())
    service._client = _FakeClient()  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    assert service.query_api == "fake-query-api"  # pyright: ignore[reportUnnecessaryComparison]
