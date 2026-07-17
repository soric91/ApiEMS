"""Envolturas cacheadas (TTL) de las lecturas más repetidas a InfluxDB.

Usadas por dashboard, consumption/export, KPIs, analytics y reportes.
NO se usan en /history (consultas exploratorias de rango arbitrario) ni en
el pipeline de tiempo real, que debe permanecer siempre fresco.
"""

from datetime import datetime, timedelta

from app.core.cache import cached
from app.models.variables import Aggregation, Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.influx import EnergyPoint, TimeSeriesPoint

ENERGY_TTL = 20
SERIES_TTL = 30


@cached(ttl_seconds=ENERGY_TTL)
async def cached_energy_total(
    repo: InfluxDataSource,
    counter: Variable,
    start: datetime,
    stop: datetime,
    device_id: str | None = None,
) -> float:
    return await repo.energy_total(counter, start, stop, device_id)


@cached(ttl_seconds=ENERGY_TTL)
async def cached_energy_series(
    repo: InfluxDataSource,
    counter: Variable,
    start: datetime,
    stop: datetime,
    every: timedelta,
    device_id: str | None = None,
) -> list[EnergyPoint]:
    return await repo.energy_series(counter, start, stop, every, device_id)


@cached(ttl_seconds=SERIES_TTL)
async def cached_instant_series(
    repo: InfluxDataSource,
    variable: Variable,
    start: datetime,
    stop: datetime,
    every: timedelta,
    aggregation: Aggregation = Aggregation.MEAN,
    device_id: str | None = None,
) -> list[TimeSeriesPoint]:
    return await repo.instant_series(variable, start, stop, every, aggregation, device_id)
