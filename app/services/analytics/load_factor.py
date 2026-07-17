"""Factor de carga de la energía importada = promedio / pico (0..1).

Solo considera muestras con POWER_ACTIVE_INST_TOTAL > 0 (importación): el
sistema no mide consumo bruto de la casa, así que el factor de carga del
"consumo total" no es calculable — este es el factor de carga de lo que
efectivamente se importa de la red.
"""

from datetime import datetime

import polars as pl

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import LoadFactorResult
from app.services.analytics.common import auto_interval, series_max, series_mean
from app.services.influx.cache import cached_instant_series


async def load_factor(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
) -> LoadFactorResult:
    empty = LoadFactorResult(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        average_import_w=None,
        peak_import_w=None,
        load_factor=None,
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

    avg = series_mean(importing)
    peak = series_max(importing)
    factor = round(avg / peak, 4) if peak > 0 else None
    return LoadFactorResult(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        average_import_w=round(avg, 2),
        peak_import_w=round(peak, 2),
        load_factor=factor,
    )
