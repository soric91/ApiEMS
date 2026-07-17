"""Carga base aproximada: percentil bajo de la potencia importada.

Proxy de "carga siempre encendida" (neveras, standby, etc.), no consumo
real de la casa aislado de la generación solar — el sistema no mide eso.
Solo tiene sentido pleno en ventanas sin excedente solar cubriendo la
carga (de noche, principalmente); se calcula igual sobre todo el rango
solicitado, filtrando a muestras de importación.
"""

from datetime import datetime

import polars as pl

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import BaseLoadResult
from app.services.analytics.common import auto_interval, series_quantile
from app.services.influx.cache import cached_instant_series

DEFAULT_PERCENTILE = 0.10


async def base_load(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
    percentile: float = DEFAULT_PERCENTILE,
) -> BaseLoadResult:
    empty = BaseLoadResult(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        percentile=percentile,
        base_load_w=None,
    )
    every = auto_interval(start, stop)
    points = await cached_instant_series(
        repo, Variable.POWER_ACTIVE_INST_TOTAL, start, stop, every, device_id=device_id
    )
    if not points:
        return empty

    series = pl.Series([p.value for p in points])
    importing = series.filter(series > 0)
    if importing.is_empty():
        return empty

    value = series_quantile(importing, percentile)
    if value is None:
        return empty
    return BaseLoadResult(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        percentile=percentile,
        base_load_w=round(value, 2),
    )
