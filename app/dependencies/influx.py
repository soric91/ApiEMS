"""Providers de inyección de dependencias para InfluxDB.

`get_influx_repository` devuelve el repositorio **acotado** a la flota de quien
llama, no el crudo. Eso hace que el recorte por cliente no sea algo que cada
endpoint tenga que recordar: es lo único que hay disponible.

El crudo sigue existiendo para los consumidores sin dueño — el detector de
alertas, que corre por cada lectura MQTT y no responde a nadie en particular.
"""

from typing import cast

from fastapi import Depends, Request

from app.dependencies.auth import CurrentFleet
from app.repositories.influx import InfluxRepository
from app.repositories.scoped import ScopedInfluxRepository


def get_unscoped_repository(request: Request) -> InfluxRepository:
    """El repositorio sin recortar. Solo para tareas internas."""
    return cast(InfluxRepository, request.app.state.influx_repo)


def get_influx_repository(
    fleet: CurrentFleet,
    inner: InfluxRepository = Depends(get_unscoped_repository),  # noqa: B008
) -> ScopedInfluxRepository:
    """El repositorio que ve un endpoint: confinado a la empresa de quien llama."""
    return ScopedInfluxRepository(inner, fleet.device_ids)
