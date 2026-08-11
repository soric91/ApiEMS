"""El repositorio que recibe un endpoint viene ya acotado a un cliente.

Nunca se ejerce vía HTTP: los tests de endpoints lo sustituyen por
FakeInfluxRepository (ver conftest.client). Se prueba acá directo, porque lo
que importa no es de dónde sale el repositorio crudo sino que lo que se
entrega sea el envoltorio y no él.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from app.dependencies.influx import get_influx_repository, get_unscoped_repository
from app.models.variables import Variable
from app.repositories.influx import InfluxRepository
from app.repositories.scoped import ScopedInfluxRepository
from app.services.crm.fleet import ClientFleet, FleetDevice
from tests.fakes import FakeInfluxRepository

MIO = "bf6a469f-4c2a-4402-9438-49a491ad2238"
AJENO = "00000000-0000-4000-8000-000000000000"


def _fleet() -> ClientFleet:
    return ClientFleet(
        client_id="cliente-1",
        devices=(
            FleetDevice(
                id=MIO,
                nombre="Medidor",
                modbus_id=1,
                sede_id="s1",
                sede="Sede",
                gateway_id="g1",
                gateway="GW-1",
                gateway_en_linea=True,
            ),
        ),
        variables=(),
        puede_ver_consumo=True,
    )


def test_the_unscoped_repository_comes_from_app_state() -> None:
    sentinel = object()
    fake_request = cast(
        Request,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(influx_repo=sentinel))),
    )

    assert get_unscoped_repository(fake_request) is sentinel


def test_an_endpoint_never_receives_the_raw_repository() -> None:
    """Si lo recibiera, una consulta sin `device_id` agregaría toda la flota
    de todas las empresas. El envoltorio es lo que lo hace imposible."""
    inner = cast(InfluxRepository, object())

    repo = get_influx_repository(_fleet(), inner)

    assert isinstance(repo, ScopedInfluxRepository)


async def test_a_device_of_another_client_is_not_found() -> None:
    """404 y no 403: confirmar que existe ya sería contar algo ajeno."""
    inner = FakeInfluxRepository()
    repo = get_influx_repository(_fleet(), cast(InfluxRepository, inner))
    start = datetime(2026, 4, 20, tzinfo=UTC)

    with pytest.raises(HTTPException) as raised:
        await repo.energy_total(
            Variable.POWER_ACTIVE_TOTAL_POS, start, start + timedelta(days=1), AJENO
        )

    assert raised.value.status_code == 404
    # Y no llegó a consultarse nada: el rechazo pasa antes de tocar InfluxDB.
    assert inner.calls == []


async def test_a_device_of_this_client_goes_through() -> None:
    """El contraste que hace útil al test anterior."""
    inner = FakeInfluxRepository()
    repo = get_influx_repository(_fleet(), cast(InfluxRepository, inner))
    start = datetime(2026, 4, 20, tzinfo=UTC)

    await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, start, start + timedelta(days=1), MIO)

    # El recorte por flota viaja con la consulta aunque se pidió un equipo
    # concreto: es la diferencia entre "confío en la validación" y "además lo
    # acoto en la propia consulta".
    assert inner.calls == [("energy_total", Variable.POWER_ACTIVE_TOTAL_POS.value, MIO, (MIO,))]


async def test_energy_records_alien_device_fails_before_streaming() -> None:
    """El 404 del CSV se rechaza ANTES de volcar la primera fila: el `_check`
    corre al construir el generador, no dentro del stream."""
    inner = FakeInfluxRepository()
    repo = get_influx_repository(_fleet(), cast(InfluxRepository, inner))
    start = datetime(2026, 4, 20, tzinfo=UTC)

    with pytest.raises(HTTPException) as raised:
        await repo.energy_records(
            (Variable.POWER_REACTIVE_QUAD1,), start, start + timedelta(days=1), AJENO
        )

    assert raised.value.status_code == 404
    assert inner.calls == []


async def test_energy_records_scope_travels_with_query() -> None:
    inner = FakeInfluxRepository()
    repo = get_influx_repository(_fleet(), cast(InfluxRepository, inner))
    start = datetime(2026, 4, 20, tzinfo=UTC)

    records = await repo.energy_records(
        (Variable.POWER_REACTIVE_QUAD1,), start, start + timedelta(days=1), MIO
    )

    assert inner.calls == [("energy_records", ("Q1Eq",), MIO, (MIO,))]
    assert [row async for row in records] == []
