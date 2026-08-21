"""Ensambla el payload de un reporte a partir de KPIs + analytics + energía."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import Settings
from app.models.variables import Aggregation, Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import BaseLoadResult, LoadFactorResult, MaxDemandResult
from app.schemas.influx import EnergyPoint
from app.schemas.kpis import KpiSummary
from app.schemas.reports import ReportData, ReportPeriod
from app.schemas.tariff import CostBreakdown, CostPeriod, TariffConfig
from app.services.analytics.common import (
    DEFAULT_PERCENTILE,
    base_load_result,
    load_factor_result,
    max_demand_result,
    power_frames,
)
from app.services.influx.cache import (
    cached_energy_series,
    cached_energy_total,
    cached_instant_series,
)
from app.services.kpis.summary import compute_kpis
from app.services.periods import EnergyBucket, PeriodBounds, resolve_period
from app.services.tariff.cost import compute_cost_from_points

_REPORT_TO_COST_PERIOD: dict[ReportPeriod, CostPeriod] = {
    "daily": "day",
    "weekly": "week",
    "monthly": "month",
    "yearly": "year",
    "custom": "custom",
}


@dataclass(frozen=True)
class ConsolidatedReport:
    """Todo lo que `build_report` y sus reutilizadores necesitan, calculado
    en UNA pasada sobre las series cacheadas (energía + un solo DataFrame de
    potencia neta compartido por KPIs, demanda máxima, factor de carga y
    carga base)."""

    period_start: datetime
    period_end: datetime
    consumption_series: list[EnergyPoint]
    export_series: list[EnergyPoint]
    consumption_total: float
    export_total: float
    kpis: KpiSummary
    max_demand: MaxDemandResult
    load_factor: LoadFactorResult
    base_load: BaseLoadResult
    costs: CostBreakdown


async def consolidate(
    repo: InfluxDataSource,
    settings: Settings,
    bounds: PeriodBounds,
    device_id: str | None,
    tariff: TariffConfig,
    cost_period: CostPeriod,
    percentile: float,
) -> ConsolidatedReport:
    """Una sola pasada sobre las series cacheadas produce energía+series,
    KPIs, max_demand, load_factor, base_load y costos.

    La serie de potencia neta (TotW) se lee UNA vez y se comparte: los tres
    indicadores de carga salen del mismo `PolarsFrame` (ver
    `analytics.common.power_frames`) y `compute_kpis` recibe los puntos ya
    leídos en vez de volver a consultarlos.
    """
    start, stop, every = bounds.start, bounds.stop, bounds.interval
    # Las barras van a su propio paso: el `every` de lectura puede ser de
    # minutos (lo necesita la demanda pico) y eso no se puede dibujar.
    bucket = bounds.energy_interval

    # Dos gathers: asyncio.gather solo tiene overloads tipados por posición
    # hasta 5 awaitables; con más, el tipo de cada resultado colapsa a la
    # unión de todos los tipos devueltos.
    consumption_series, export_series, consumption_total, export_total = await asyncio.gather(
        cached_energy_series(repo, Variable.POWER_ACTIVE_TOTAL_POS, start, stop, bucket, device_id),
        cached_energy_series(repo, Variable.POWER_ACTIVE_TOTAL_NEG, start, stop, bucket, device_id),
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_POS, start, stop, device_id),
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_NEG, start, stop, device_id),
    )

    # DOS lecturas de la misma variable, con agregación distinta y a propósito:
    #   - mean: promedios (KPI de potencia, media de importación, carga base).
    #   - max:  la demanda pico. Promediar la ventana la borra — con ~500
    #     puntos, 30 días dan ventanas de ~86 min y un pico de tres minutos
    #     desaparecía dentro del promedio.
    # Las dos van cacheadas (SERIES_TTL) y en paralelo, así que el costo es una
    # consulta más, no el doble de latencia.
    power_points, peak_points = await asyncio.gather(
        cached_instant_series(
            repo, Variable.POWER_ACTIVE_INST_TOTAL, start, stop, every, device_id=device_id
        ),
        cached_instant_series(
            repo,
            Variable.POWER_ACTIVE_INST_TOTAL,
            start,
            stop,
            every,
            Aggregation.MAX,
            device_id=device_id,
        ),
    )
    # Polars es CPU síncrono (power_frames y los *_result): se corre fuera del
    # event loop (ver skill fastapi — "blocking code is not run inside of
    # async functions").
    (_, importing), (_, importing_peak) = await asyncio.gather(
        asyncio.to_thread(power_frames, power_points),
        asyncio.to_thread(power_frames, peak_points),
    )
    # El pico se resuelve primero porque el factor de carga lo necesita: media
    # del marco promediado sobre el pico real.
    max_demand = await asyncio.to_thread(max_demand_result, start, stop, device_id, importing_peak)
    load_factor, base_load = await asyncio.gather(
        asyncio.to_thread(
            load_factor_result, start, stop, device_id, importing, max_demand.peak_power_w
        ),
        asyncio.to_thread(base_load_result, start, stop, device_id, importing, percentile),
    )
    kpis = await compute_kpis(
        repo,
        settings,
        start,
        stop,
        device_id,
        power_points=power_points,
        peak_points=peak_points,
    )
    costs = compute_cost_from_points(
        tariff,
        cost_period,
        start,
        stop,
        device_id,
        consumption_series,
        export_series,
        consumption_total,
        export_total,
    )

    return ConsolidatedReport(
        period_start=start,
        period_end=stop,
        consumption_series=consumption_series,
        export_series=export_series,
        consumption_total=consumption_total,
        export_total=export_total,
        kpis=kpis,
        max_demand=max_demand,
        load_factor=load_factor,
        base_load=base_load,
        costs=costs,
    )


async def build_report(
    repo: InfluxDataSource,
    settings: Settings,
    report_type: ReportPeriod,
    device_id: str | None,
    tariff: TariffConfig,
    start: datetime | None = None,
    stop: datetime | None = None,
    bucket: EnergyBucket | None = None,
) -> ReportData:
    now = datetime.now(tz=UTC)
    bounds = resolve_period(
        report_type, settings.TIMEZONE, now, from_=start, to=stop, bucket=bucket
    )
    report = await consolidate(
        repo,
        settings,
        bounds,
        device_id,
        tariff,
        _REPORT_TO_COST_PERIOD[report_type],
        percentile=DEFAULT_PERCENTILE,
    )

    return ReportData(
        report_type=report_type,
        device_id=device_id,
        period_start=report.period_start,
        period_end=report.period_end,
        consumption_kwh=report.consumption_total,
        export_kwh=report.export_total,
        net_balance_kwh=round(report.consumption_total - report.export_total, 2),
        consumption_series=report.consumption_series,
        export_series=report.export_series,
        kpis=report.kpis,
        max_demand=report.max_demand,
        load_factor=report.load_factor,
        base_load=report.base_load,
        costs=report.costs,
        generated_at=now,
    )
