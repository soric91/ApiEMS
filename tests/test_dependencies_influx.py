"""get_influx_repository() nunca se ejerce vía HTTP: los tests de endpoints
siempre lo sustituyen por FakeInfluxRepository (ver conftest.client). Se
prueba aquí directo, con un objeto mínimo que imita el Request real."""

from types import SimpleNamespace
from typing import cast

from fastapi import Request

from app.dependencies.influx import get_influx_repository


def test_get_influx_repository_reads_app_state() -> None:
    sentinel = object()
    fake_request = cast(
        Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(influx_repo=sentinel)))
    )
    assert get_influx_repository(fake_request) is sentinel
