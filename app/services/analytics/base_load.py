"""Carga base aproximada: percentil bajo de la potencia importada.

Proxy de "carga siempre encendida" (neveras, standby, etc.), no consumo
real de la casa aislado de la generación solar — el sistema no mide eso.
Solo tiene sentido pleno en ventanas sin excedente solar cubriendo la
carga (de noche, principalmente); se calcula igual sobre todo el rango
solicitado, filtrando a muestras de importación.
"""

from datetime import datetime

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import BaseLoadResult
from app.services.analytics.common import auto_interval, base_load_result, power_frames
from app.services.influx.cache import cached_instant_series

DEFAULT_PERCENTILE = 0.10


async def base_load(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
    percentile: float = DEFAULT_PERCENTILE,
) -> BaseLoadResult:
    every = auto_interval(start, stop)
    points = await cached_instant_series(
        repo, Variable.POWER_ACTIVE_INST_TOTAL, start, stop, every, device_id=device_id
    )
    _, importing = power_frames(points)
    return base_load_result(start, stop, device_id, importing, percentile)
