from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.cache import clear_all_caches
from app.core.config import get_settings
from app.dependencies.influx import get_influx_repository
from app.main import create_app
from tests.fakes import FakeInfluxRepository, FakeInfluxService


@pytest.fixture(autouse=True)
def _clear_ttl_caches() -> None:  # pyright: ignore[reportUnusedFunction]
    # Las TTLCache de @cached son globales al módulo; sin esto, un objeto
    # FakeInfluxRepository de un test previo cuyo id() de memoria fue
    # reutilizado por el GC podría "acertar" una clave de cache ajena.
    clear_all_caches()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret-key-0123456789abcdef")
    monkeypatch.setenv("API_USERNAME", "testuser")
    monkeypatch.setenv("API_PASSWORD", "testpass")
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def fake_influx_repo() -> FakeInfluxRepository:
    return FakeInfluxRepository()


@pytest.fixture
def client(app: FastAPI, fake_influx_repo: FakeInfluxRepository) -> Iterator[TestClient]:
    # Los endpoints reales de InfluxDB se sustituyen por dobles en memoria:
    # el cliente real solo se ejercita en los smoke tests manuales.
    app.dependency_overrides[get_influx_repository] = lambda: fake_influx_repo
    with TestClient(app) as test_client:
        app.state.influx = FakeInfluxService()  # evita ping() real tras el lifespan
        yield test_client
    app.dependency_overrides.clear()
