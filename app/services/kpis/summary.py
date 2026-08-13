"""KPIs: estadísticas instantáneas (Polars) + energía por periodo."""

import asyncio
from datetime import datetime

import polars as pl

from app.core.config import Settings
from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.influx import TimeSeriesPoint
from app.schemas.kpis import KpiSummary
from app.services.analytics.common import auto_interval, series_max, series_mean, series_min
from app.services.influx.cache import cached_energy_total, cached_instant_series
from app.utils.period import start_of_day, start_of_month, start_of_week


def _stats(points: list[TimeSeriesPoint]) -> tuple[float | None, float | None, float | None]:
    """(avg, max, min) redondeados con Polars; None si no hay muestras."""
    if not points:
        return None, None, None
    series = pl.Series([p.value for p in points])
    return round(series_mean(series), 2), round(series_max(series), 2), round(series_min(series), 2)


async def compute_kpis(
    repo: InfluxDataSource,
    settings: Settings,
    start: datetime,
    stop: datetime,
    device_id: str | None,
    power_points: list[TimeSeriesPoint] | None = None,
) -> KpiSummary:
    every = auto_interval(start, stop)

    if power_points is None:
        power_points = await cached_instant_series(
            repo, Variable.POWER_ACTIVE_INST_TOTAL, start, stop, every, device_id=device_id
        )
    (
        voltage_a_points,
        voltage_b_points,
        voltage_c_points,
        current_a_points,
        current_b_points,
        current_c_points,
        pf_points,
    ) = await asyncio.gather(
        cached_instant_series(repo, Variable.VOLTAGE_A, start, stop, every, device_id=device_id),
        cached_instant_series(repo, Variable.VOLTAGE_B, start, stop, every, device_id=device_id),
        cached_instant_series(repo, Variable.VOLTAGE_C, start, stop, every, device_id=device_id),
        cached_instant_series(repo, Variable.CURRENT_A, start, stop, every, device_id=device_id),
        cached_instant_series(repo, Variable.CURRENT_B, start, stop, every, device_id=device_id),
        cached_instant_series(repo, Variable.CURRENT_C, start, stop, every, device_id=device_id),
        cached_instant_series(
            repo, Variable.FACTOR_POTENCIA_TOTAL, start, stop, every, device_id=device_id
        ),
    )

    # Polars es CPU síncrono: las cuatro reducciones se corren en threads,
    # no en el event loop (ver skill fastapi).
    power_avg, power_max, _ = await asyncio.to_thread(
        _stats, power_points
    )
    voltage_avg, voltage_max, voltage_min = await asyncio.to_thread(
        _stats, voltage_a_points + voltage_b_points + voltage_c_points
    )
    current_avg, _, _ = await asyncio.to_thread(
        _stats, current_a_points + current_b_points + current_c_points
    )
    pf_avg, _, _ = await asyncio.to_thread(_stats, pf_points)

    # Cuantías SIEMPRE dentro del rango pedido [start, stop]: los límites
    # día/semana/mes se alinean a `stop` y se recortan a `start`, de modo que
    # consultar KPIs de un rango pasado no arrastra consumo de "hoy".
    day_start = max(start_of_day(settings.TIMEZONE, stop), start)
    week_start = max(start_of_week(settings.TIMEZONE, stop), start)
    month_start = max(start_of_month(settings.TIMEZONE, stop), start)

    (
        consumption_daily,
        consumption_weekly,
        consumption_monthly,
        export_daily,
        export_monthly,
    ) = await asyncio.gather(
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_POS, day_start, stop, device_id),
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_POS, week_start, stop, device_id),
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_POS, month_start, stop, device_id),
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_NEG, day_start, stop, device_id),
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_NEG, month_start, stop, device_id),
    )

    return KpiSummary(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        power_avg_w=power_avg,
        power_max_w=power_max,
        voltage_avg_v=voltage_avg,
        voltage_min_v=voltage_min,
        voltage_max_v=voltage_max,
        current_avg_a=current_avg,
        power_factor_avg=pf_avg,
        consumption_daily_kwh=consumption_daily,
        consumption_weekly_kwh=consumption_weekly,
        consumption_monthly_kwh=consumption_monthly,
        export_daily_kwh=export_daily,
        export_monthly_kwh=export_monthly,
    )
