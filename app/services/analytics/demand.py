"""Demanda máxima: pico de potencia importada de la red, con timestamp."""

from datetime import datetime

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import MaxDemandResult
from app.services.analytics.common import auto_interval, max_demand_result, power_frames
from app.services.influx.cache import cached_instant_series


async def max_demand(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
) -> MaxDemandResult:
    every = auto_interval(start, stop)
    points = await cached_instant_series(
        repo, Variable.POWER_ACTIVE_INST_TOTAL, start, stop, every, device_id=device_id
    )
    _, importing = power_frames(points)
    return max_demand_result(start, stop, device_id, importing)
