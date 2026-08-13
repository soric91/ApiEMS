"""Comparación de dos periodos: consumo y exportación.

A propósito SIN demanda pico: calcularla escaneaba la serie de potencia
instantánea del rango entero (hasta 60 días en las tarjetas del panel) para
un campo que ningún consumidor muestra. La comparación solo necesita los
totales de energía, que salen de contadores (baratos y cacheados).
"""

import asyncio
from datetime import datetime

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import ComparePeriod, CompareResult
from app.services.influx.cache import cached_energy_total


async def _period_stats(
    repo: InfluxDataSource, start: datetime, stop: datetime, device_id: str | None
) -> ComparePeriod:
    consumption, export = await asyncio.gather(
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_POS, start, stop, device_id),
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_NEG, start, stop, device_id),
    )
    return ComparePeriod(
        period_start=start,
        period_end=stop,
        consumption_kwh=consumption,
        export_kwh=export,
    )


def _delta_pct(a: float, b: float) -> float | None:
    if a == 0:
        return None
    return round((b - a) / a * 100, 2)


async def compare_periods(
    repo: InfluxDataSource,
    start_a: datetime,
    stop_a: datetime,
    start_b: datetime,
    stop_b: datetime,
    device_id: str | None,
) -> CompareResult:
    period_a, period_b = await asyncio.gather(
        _period_stats(repo, start_a, stop_a, device_id),
        _period_stats(repo, start_b, stop_b, device_id),
    )
    return CompareResult(
        device_id=device_id,
        period_a=period_a,
        period_b=period_b,
        consumption_delta_pct=_delta_pct(period_a.consumption_kwh, period_b.consumption_kwh),
        export_delta_pct=_delta_pct(period_a.export_kwh, period_b.export_kwh),
    )
