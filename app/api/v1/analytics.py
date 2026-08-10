"""Analytics: perfiles, demanda, factor de carga, carga base, comparación.

Todos los indicadores de carga (max-demand, load-factor, base-load) se
calculan solo sobre POWER_ACTIVE_INST_TOTAL > 0 (importación de la red):
el sistema no mide consumo bruto de la casa, así que durante exportación
esos indicadores no están definidos.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentFleet
from app.dependencies.influx import get_influx_repository
from app.dependencies.tariff import get_tariff_config
from app.models.variables import Variable
from app.repositories.scoped import ScopedInfluxRepository
from app.schemas.analytics import (
    AnalyticsOverview,
    AnalyticsSummary,
    BaseLoadResult,
    CompareResult,
    HourProfilePoint,
    LoadFactorResult,
    MaxDemandResult,
    WeekdayProfilePoint,
)
from app.schemas.common import ApiResponse
from app.schemas.tariff import TariffConfig
from app.services.analytics.base_load import DEFAULT_PERCENTILE, base_load
from app.services.analytics.compare import compare_periods
from app.services.analytics.demand import max_demand
from app.services.analytics.load_factor import load_factor
from app.services.analytics.profile import daily_profile, weekday_profile
from app.services.analytics.summary import analytics_summary
from app.services.influx.cache import cached_energy_total
from app.services.periods import PeriodBounds, resolve_period

router = APIRouter(prefix="/analytics", tags=["Analytics"])

# El perfil horario (hora de mayor consumo/exportación) necesita varias
# semanas de muestras por hora para ser representativo — a diferencia de los
# demás endpoints de /analytics, "hoy" (el default de _resolve_range) no
# alcanza para eso, por eso /summary tiene su propio default más largo.
SUMMARY_LOOKBACK_DAYS = 30

RepoDep = Annotated[ScopedInfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
TariffDep = Annotated[TariffConfig, Depends(get_tariff_config)]
FromQuery = Annotated[
    datetime | None, Query(alias="from", description="Inicio del rango (UTC). Por defecto: hoy.")
]
ToQuery = Annotated[datetime | None, Query(description="Fin del rango (UTC). Por defecto: ahora.")]


def _resolve_range(settings: Settings, from_: datetime | None, to: datetime | None) -> PeriodBounds:
    """Rango del endpoint por defecto (hoy) con las sobreescrituras from/to."""
    try:
        return resolve_period("day", settings.TIMEZONE, from_=from_, to=to)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get(
    "",
    summary="Resumen de analytics",
    response_model=ApiResponse[AnalyticsOverview],
    deprecated=True,  # subconjunto literal de /reports/daily (max_demand/load_factor/base_load)
)
async def analytics_overview(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[AnalyticsOverview]:
    """Consumo, exportación, demanda pico, factor de carga y carga base del
    periodo (por defecto: hoy)."""
    bounds = _resolve_range(settings, from_, to)
    consumption, export, demand, lf, bl = await asyncio.gather(
        cached_energy_total(
            repo, Variable.POWER_ACTIVE_TOTAL_POS, bounds.start, bounds.stop, device_id
        ),
        cached_energy_total(
            repo, Variable.POWER_ACTIVE_TOTAL_NEG, bounds.start, bounds.stop, device_id
        ),
        max_demand(repo, bounds.start, bounds.stop, device_id),
        load_factor(repo, bounds.start, bounds.stop, device_id),
        base_load(repo, bounds.start, bounds.stop, device_id),
    )
    return ApiResponse(
        data=AnalyticsOverview(
            period_start=bounds.start,
            period_end=bounds.stop,
            device_id=device_id,
            consumption_kwh=consumption,
            export_kwh=export,
            max_demand=demand,
            load_factor=lf,
            base_load=bl,
        )
    )


@router.get(
    "/daily-profile",
    summary="Perfil horario típico",
    response_model=ApiResponse[list[HourProfilePoint]],
)
async def analytics_daily_profile(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[list[HourProfilePoint]]:
    """Curva típica de potencia neta por hora del día (0-23), promediando
    todos los días del rango solicitado (por defecto: hoy)."""
    bounds = _resolve_range(settings, from_, to)
    return ApiResponse(
        data=await daily_profile(repo, bounds.start, bounds.stop, device_id, settings.TIMEZONE)
    )


@router.get(
    "/monthly-profile",
    summary="Perfil semanal de consumo/exportación",
    response_model=ApiResponse[list[WeekdayProfilePoint]],
)
async def analytics_monthly_profile(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[list[WeekdayProfilePoint]]:
    """Consumo y exportación promedio por día de la semana (lunes..domingo)
    en el rango solicitado (por defecto: el mes en curso)."""
    try:
        bounds = resolve_period("month", settings.TIMEZONE, from_=from_, to=to)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ApiResponse(
        data=await weekday_profile(repo, bounds.start, bounds.stop, device_id, settings.TIMEZONE)
    )


@router.get(
    "/max-demand",
    summary="Demanda máxima (pico de importación)",
    response_model=ApiResponse[MaxDemandResult],
)
async def analytics_max_demand(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[MaxDemandResult]:
    """Pico de potencia importada de la red en el periodo, con su timestamp."""
    bounds = _resolve_range(settings, from_, to)
    return ApiResponse(data=await max_demand(repo, bounds.start, bounds.stop, device_id))


@router.get(
    "/load-factor",
    summary="Factor de carga de la importación",
    response_model=ApiResponse[LoadFactorResult],
)
async def analytics_load_factor(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[LoadFactorResult]:
    """`promedio / pico` de la potencia importada (0..1); más cerca de 1 =
    demanda más plana. Solo considera ventanas de importación."""
    bounds = _resolve_range(settings, from_, to)
    return ApiResponse(data=await load_factor(repo, bounds.start, bounds.stop, device_id))


@router.get(
    "/base-load", summary="Carga base aproximada", response_model=ApiResponse[BaseLoadResult]
)
async def analytics_base_load(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    percentile: Annotated[float, Query(gt=0, lt=1)] = DEFAULT_PERCENTILE,
    device_id: str | None = None,
) -> ApiResponse[BaseLoadResult]:
    """Percentil bajo (por defecto P10) de la potencia importada — proxy de
    la carga siempre encendida. No es consumo real aislado de la generación
    solar: el sistema no mide eso."""
    bounds = _resolve_range(settings, from_, to)
    return ApiResponse(data=await base_load(repo, bounds.start, bounds.stop, device_id, percentile))


@router.get("/compare", summary="Comparar dos periodos", response_model=ApiResponse[CompareResult])
async def analytics_compare(
    repo: RepoDep,
    fleet: CurrentFleet,
    from_a: Annotated[datetime, Query(description="Inicio del periodo A (UTC)")],
    to_a: Annotated[datetime, Query(description="Fin del periodo A (UTC)")],
    from_b: Annotated[datetime, Query(description="Inicio del periodo B (UTC)")],
    to_b: Annotated[datetime, Query(description="Fin del periodo B (UTC)")],
    device_id: str | None = None,
) -> ApiResponse[CompareResult]:
    """Compara consumo, exportación y demanda pico entre dos periodos
    arbitrarios (p. ej. esta semana vs. la anterior)."""
    if from_a >= to_a or from_b >= to_b:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "cada rango debe tener 'from' anterior a 'to'"
        )
    return ApiResponse(data=await compare_periods(repo, from_a, to_a, from_b, to_b, device_id))


@router.get(
    "/summary",
    summary="Resumen general (consumo, exportación, horas pico, eficiencia)",
    response_model=ApiResponse[AnalyticsSummary],
)
async def analytics_summary_endpoint(
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[AnalyticsSummary]:
    """Consumo/exportación diario-semanal-mensual, hora típica de mayor
    consumo y de mayor exportación (perfil horario sobre el rango, por
    defecto últimos 30 días), y cuánto se habría ahorrado en COP si la
    energía exportada este mes se hubiera autoconsumido a la tarifa
    vigente. Pensado para el botón "exportar resumen" de Analítica."""
    now = datetime.now(tz=UTC)
    default_start = now - timedelta(days=SUMMARY_LOOKBACK_DAYS)
    try:
        bounds = resolve_period("day", settings.TIMEZONE, now, from_=from_ or default_start, to=to)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ApiResponse(
        data=await analytics_summary(repo, settings, bounds.start, bounds.stop, device_id, tariff)
    )
