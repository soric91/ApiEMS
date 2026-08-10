"""Envolturas cacheadas (TTL) de las lecturas más repetidas a InfluxDB.

Usadas por dashboard, consumption/export, KPIs, analytics y reportes.
NO se usan en /history (consultas exploratorias de rango arbitrario) ni en
el pipeline de tiempo real, que debe permanecer siempre fresco.
"""

from datetime import datetime, timedelta

from app.core.cache import cached
from app.models.variables import Aggregation, Variable
from app.repositories.influx import InfluxDataSource
from app.repositories.scoped import ScopedInfluxRepository
from app.schemas.influx import EnergyPoint, TimeSeriesPoint

ENERGY_TTL = 20
SERIES_TTL = 30

# Qué variables tienen datos cambia cuando alguien da de alta un medidor en el
# CRM, no entre una carga del panel y la siguiente. Y averiguarlo es la consulta
# más cara del proyecto: `schema.fieldKeys` con predicado no lee el índice pese
# al nombre —por debajo hace `range |> filter |> keys |> distinct`— así que
# barre datos. Con un medidor a 1 Hz eso son millones de puntos por una
# respuesta que ya se sabía.
#
# Una hora: el precio es que una variable recién cargada tarde hasta ese rato en
# aparecer, y se carga a mano en el CRM.
FIELD_KEYS_TTL = 3600


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


@cached(ttl_seconds=FIELD_KEYS_TTL)
async def cached_field_keys(
    # Acotado y no `InfluxDataSource`: el protocolo compartido no declara
    # `field_keys` porque el repositorio crudo la recibe con la flota por
    # parámetro y el acotado ya la lleva adentro. Firmas distintas, un solo
    # llamador.
    repo: ScopedInfluxRepository,
    lookback: timedelta,
) -> list[str]:
    """Qué campos reportaron algo, cacheado por empresa.

    La clave incluye la identidad de la flota (ver `ScopedInfluxRepository.
    cache_identity`): sin eso, esta caché serviría las variables de una empresa
    a otra, que es peor que la lentitud que viene a resolver.
    """
    return await repo.field_keys(lookback)
