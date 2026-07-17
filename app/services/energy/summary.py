"""Resumen de energía (importada o exportada) por periodo.

Compartido por /consumption y /export, que solo difieren en qué contador
consultan (POWER_ACTIVE_TOTAL_POS vs _NEG) — misma lógica de límites de
periodo y desglose.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.energy import EnergySummary, Period
from app.schemas.influx import EnergyPoint
from app.services.influx.cache import cached_energy_series, cached_energy_total
from app.utils.period import (
    month_starts_of_year,
    start_of_day,
    start_of_month,
    start_of_week,
)

_DAY_INTERVAL = timedelta(hours=1)
_WEEK_MONTH_INTERVAL = timedelta(days=1)


async def period_summary(
    repo: InfluxDataSource,
    counter: Variable,
    period: Period,
    tz_name: str,
    device_id: str | None,
) -> EnergySummary:
    now = datetime.now(tz=UTC)

    if period == "year":
        return await _year_summary(repo, counter, tz_name, device_id, now)

    period_start, interval = _bounds(period, tz_name)
    series, total = await asyncio.gather(
        cached_energy_series(repo, counter, period_start, now, interval, device_id),
        cached_energy_total(repo, counter, period_start, now, device_id),
    )
    return EnergySummary(
        period=period,
        device_id=device_id,
        period_start=period_start,
        period_end=now,
        total_kwh=total,
        series=series,
    )


def _bounds(period: Period, tz_name: str) -> tuple[datetime, timedelta]:
    if period == "day":
        return start_of_day(tz_name), _DAY_INTERVAL
    if period == "week":
        return start_of_week(tz_name), _WEEK_MONTH_INTERVAL
    return start_of_month(tz_name), _WEEK_MONTH_INTERVAL  # "month"


async def _year_summary(
    repo: InfluxDataSource,
    counter: Variable,
    tz_name: str,
    device_id: str | None,
    now: datetime,
) -> EnergySummary:
    """Desglose mensual: 12 totales exactos (spread por mes calendario) en
    lugar de aggregateWindow, porque los meses no tienen duración fija.
    """
    starts = month_starts_of_year(tz_name, now)
    boundaries = list(zip(starts, [*starts[1:], now], strict=True))
    totals = await asyncio.gather(
        *(cached_energy_total(repo, counter, start, end, device_id) for start, end in boundaries)
    )
    series = [
        EnergyPoint(time=start, value=value)
        for (start, _), value in zip(boundaries, totals, strict=True)
    ]
    return EnergySummary(
        period="year",
        device_id=device_id,
        period_start=starts[0],
        period_end=now,
        total_kwh=round(sum(totals), 2),
        series=series,
    )
