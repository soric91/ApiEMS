"""Factor de carga de la energía importada = promedio / pico (0..1).

Solo considera muestras con POWER_ACTIVE_INST_TOTAL > 0 (importación): el
sistema no mide consumo bruto de la casa, así que el factor de carga del
"consumo total" no es calculable — este es el factor de carga de lo que
efectivamente se importa de la red.
"""

import asyncio
from datetime import datetime

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import LoadFactorResult
from app.services.analytics.common import auto_interval, load_factor_result, power_frames
from app.services.influx.cache import cached_instant_series


async def load_factor(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
) -> LoadFactorResult:
    every = auto_interval(start, stop)
    points = await cached_instant_series(
        repo, Variable.POWER_ACTIVE_INST_TOTAL, start, stop, every, device_id=device_id
    )
    # Polars es CPU síncrono: se corre fuera del event loop.
    _, importing = await asyncio.to_thread(power_frames, points)
    return await asyncio.to_thread(load_factor_result, start, stop, device_id, importing)
