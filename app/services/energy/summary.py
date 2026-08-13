"""Resumen de energía (importada o exportada) por periodo.

Sirve al cálculo de costos por preset de periodo (day/week/month/year) y
conserva el caché de meses cerrados; los endpoints HTTP de consumo/exportación
que lo consumían ya no existen (fase V3) y hoy el contrato pasa por /reports.
"""

import asyncio
from datetime import UTC, datetime

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.energy import EnergySummary, Period
from app.schemas.influx import EnergyPoint
from app.services.influx.cache import (
    cached_closed_month_total,
    cached_energy_series,
    cached_energy_total,
)
from app.services.periods import resolve_period
from app.utils.period import month_starts_of_year


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

    bounds = resolve_period(period, tz_name, now)
    series, total = await asyncio.gather(
        cached_energy_series(repo, counter, bounds.start, now, bounds.interval, device_id),
        cached_energy_total(repo, counter, bounds.start, now, device_id),
    )
    return EnergySummary(
        period=period,
        device_id=device_id,
        period_start=bounds.start,
        period_end=now,
        total_kwh=total,
        series=series,
    )


async def _year_summary(
    repo: InfluxDataSource,
    counter: Variable,
    tz_name: str,
    device_id: str | None,
    now: datetime,
) -> EnergySummary:
    """Desglose mensual: 12 totales exactos (spread por mes calendario) en
    lugar de aggregateWindow, porque los meses no tienen duración fija.
    Los meses ya cerrados son inmutables: la suma va a la caché de largo
    plazo (`cached_closed_month_total`) y no se vuelve a tocar InfluxDB en 7
    días; solo el mes en curso se relee siempre.
    """
    starts = month_starts_of_year(tz_name, now)
    boundaries = list(zip(starts, [*starts[1:], now], strict=True))
    totals = await asyncio.gather(
        *(
            cached_closed_month_total(repo, counter, start, end, device_id)
            if end < now
            else cached_energy_total(repo, counter, start, end, device_id)
            for start, end in boundaries
        )
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
