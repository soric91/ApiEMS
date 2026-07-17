"""Demanda máxima: pico de potencia importada de la red, con timestamp."""

from datetime import datetime

import polars as pl

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import MaxDemandResult
from app.services.analytics.common import auto_interval
from app.services.influx.cache import cached_instant_series


async def max_demand(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
) -> MaxDemandResult:
    empty = MaxDemandResult(
        period_start=start, period_end=stop, device_id=device_id, peak_power_w=None, peak_at=None
    )
    every = auto_interval(start, stop)
    points = await cached_instant_series(
        repo, Variable.POWER_ACTIVE_INST_TOTAL, start, stop, every, device_id=device_id
    )
    if not points:
        return empty

    df = pl.DataFrame({"time": [p.time for p in points], "value": [p.value for p in points]})
    importing: pl.DataFrame = df.filter(pl.col("value") > 0)  # pyright: ignore[reportUnknownMemberType]
    if importing.is_empty():
        return empty

    sorted_df: pl.DataFrame = importing.sort(  # pyright: ignore[reportUnknownMemberType]
        "value", descending=True
    )
    return MaxDemandResult(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        peak_power_w=round(float(sorted_df["value"][0]), 2),
        peak_at=sorted_df["time"][0],
    )
