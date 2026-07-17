"""Ensambla el payload de un reporte a partir de KPIs + analytics + energía."""

import asyncio
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.reports import ReportData, ReportPeriod
from app.schemas.tariff import CostPeriod
from app.services.analytics.base_load import base_load
from app.services.analytics.common import auto_interval
from app.services.analytics.demand import max_demand
from app.services.analytics.load_factor import load_factor
from app.services.influx.cache import cached_energy_series, cached_energy_total
from app.services.kpis.summary import compute_kpis
from app.services.tariff.cost import compute_cost_from_points
from app.services.tariff.store import load_tariff_config
from app.utils.period import start_of_day, start_of_month, start_of_week, start_of_year

_COST_WITH_CARGO_FIJO: frozenset[ReportPeriod] = frozenset({"monthly", "yearly", "custom"})
_REPORT_TO_COST_PERIOD: dict[ReportPeriod, CostPeriod] = {
    "daily": "day",
    "weekly": "week",
    "monthly": "month",
    "yearly": "year",
    "custom": "custom",
}

_DAY_INTERVAL = timedelta(hours=1)
_LONG_INTERVAL = timedelta(days=1)


def _fixed_bounds(
    report_type: ReportPeriod, tz_name: str, now: datetime
) -> tuple[datetime, timedelta]:
    if report_type == "daily":
        return start_of_day(tz_name, now), _DAY_INTERVAL
    if report_type == "weekly":
        return start_of_week(tz_name, now), _LONG_INTERVAL
    if report_type == "monthly":
        return start_of_month(tz_name, now), _LONG_INTERVAL
    return start_of_year(tz_name, now), _LONG_INTERVAL  # "yearly"


async def build_report(
    repo: InfluxDataSource,
    settings: Settings,
    report_type: ReportPeriod,
    device_id: str | None,
    start: datetime | None = None,
    stop: datetime | None = None,
) -> ReportData:
    now = datetime.now(tz=UTC)
    if report_type == "custom":
        if start is None or stop is None:
            raise ValueError("'custom' requiere start y stop")
        period_start, period_end = start, stop
        interval = auto_interval(period_start, period_end)
    else:
        period_start, interval = _fixed_bounds(report_type, settings.TIMEZONE, now)
        period_end = now

    # Dos gathers: asyncio.gather solo tiene overloads tipados por posición
    # hasta 5 awaitables; con más, el tipo de cada resultado colapsa a la
    # unión de todos los tipos devueltos.
    consumption_series, export_series, consumption_total, export_total = await asyncio.gather(
        cached_energy_series(
            repo, Variable.POWER_ACTIVE_TOTAL_POS, period_start, period_end, interval, device_id
        ),
        cached_energy_series(
            repo, Variable.POWER_ACTIVE_TOTAL_NEG, period_start, period_end, interval, device_id
        ),
        cached_energy_total(
            repo, Variable.POWER_ACTIVE_TOTAL_POS, period_start, period_end, device_id
        ),
        cached_energy_total(
            repo, Variable.POWER_ACTIVE_TOTAL_NEG, period_start, period_end, device_id
        ),
    )
    kpis, demand, load_factor_result, base_load_result, tariff = await asyncio.gather(
        compute_kpis(repo, settings, period_start, period_end, device_id),
        max_demand(repo, period_start, period_end, device_id),
        load_factor(repo, period_start, period_end, device_id),
        base_load(repo, period_start, period_end, device_id),
        load_tariff_config(settings.TARIFF_CONFIG_PATH),
    )
    costs = compute_cost_from_points(
        tariff,
        _REPORT_TO_COST_PERIOD[report_type],
        period_start,
        period_end,
        device_id,
        consumption_series,
        export_series,
        consumption_total,
        export_total,
        include_cargo_fijo=report_type in _COST_WITH_CARGO_FIJO,
    )

    return ReportData(
        report_type=report_type,
        device_id=device_id,
        period_start=period_start,
        period_end=period_end,
        consumption_kwh=consumption_total,
        export_kwh=export_total,
        net_balance_kwh=round(consumption_total - export_total, 2),
        consumption_series=consumption_series,
        export_series=export_series,
        kpis=kpis,
        max_demand=demand,
        load_factor=load_factor_result,
        base_load=base_load_result,
        costs=costs,
        generated_at=now,
    )
