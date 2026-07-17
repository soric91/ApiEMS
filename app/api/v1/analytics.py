"""Analytics: perfiles, demanda, factor de carga, carga base, comparación.

Todos los indicadores de carga (max-demand, load-factor, base-load) se
calculan solo sobre POWER_ACTIVE_INST_TOTAL > 0 (importación de la red):
el sistema no mide consumo bruto de la casa, así que durante exportación
esos indicadores no están definidos.
"""

import asyncio
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentUser
from app.dependencies.influx import get_influx_repository
from app.models.variables import Variable
from app.repositories.influx import InfluxRepository
from app.schemas.analytics import (
    AnalyticsOverview,
    BaseLoadResult,
    CompareResult,
    HourProfilePoint,
    LoadFactorResult,
    MaxDemandResult,
    WeekdayProfilePoint,
)
from app.schemas.common import ApiResponse
from app.services.analytics.base_load import DEFAULT_PERCENTILE, base_load
from app.services.analytics.compare import compare_periods
from app.services.analytics.demand import max_demand
from app.services.analytics.load_factor import load_factor
from app.services.analytics.profile import daily_profile, weekday_profile
from app.services.influx.cache import cached_energy_total
from app.utils.period import start_of_day, start_of_month

router = APIRouter(prefix="/analytics", tags=["Analytics"])

RepoDep = Annotated[InfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
FromQuery = Annotated[
    datetime | None, Query(alias="from", description="Inicio del rango (UTC). Por defecto: hoy.")
]
ToQuery = Annotated[datetime | None, Query(description="Fin del rango (UTC). Por defecto: ahora.")]


def _resolve_range(
    settings: Settings, from_: datetime | None, to: datetime | None
) -> tuple[datetime, datetime]:
    now = datetime.now(tz=UTC)
    start = from_ or start_of_day(settings.TIMEZONE, now)
    stop = to or now
    if start >= stop:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'from' debe ser anterior a 'to'")
    return start, stop


@router.get("", summary="Resumen de analytics", response_model=ApiResponse[AnalyticsOverview])
async def analytics_overview(
    repo: RepoDep,
    settings: SettingsDep,
    _user: CurrentUser,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[AnalyticsOverview]:
    """Consumo, exportación, demanda pico, factor de carga y carga base del
    periodo (por defecto: hoy)."""
    start, stop = _resolve_range(settings, from_, to)
    consumption, export, demand, lf, bl = await asyncio.gather(
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_POS, start, stop, device_id),
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_NEG, start, stop, device_id),
        max_demand(repo, start, stop, device_id),
        load_factor(repo, start, stop, device_id),
        base_load(repo, start, stop, device_id),
    )
    return ApiResponse(
        data=AnalyticsOverview(
            period_start=start,
            period_end=stop,
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
    _user: CurrentUser,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[list[HourProfilePoint]]:
    """Curva típica de potencia neta por hora del día (0-23), promediando
    todos los días del rango solicitado (por defecto: hoy)."""
    start, stop = _resolve_range(settings, from_, to)
    return ApiResponse(data=await daily_profile(repo, start, stop, device_id))


@router.get(
    "/monthly-profile",
    summary="Perfil semanal de consumo/exportación",
    response_model=ApiResponse[list[WeekdayProfilePoint]],
)
async def analytics_monthly_profile(
    repo: RepoDep,
    settings: SettingsDep,
    _user: CurrentUser,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[list[WeekdayProfilePoint]]:
    """Consumo y exportación promedio por día de la semana (lunes..domingo)
    en el rango solicitado (por defecto: el mes en curso)."""
    now = datetime.now(tz=UTC)
    start = from_ or start_of_month(settings.TIMEZONE, now)
    stop = to or now
    if start >= stop:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'from' debe ser anterior a 'to'")
    return ApiResponse(data=await weekday_profile(repo, start, stop, device_id))


@router.get(
    "/max-demand",
    summary="Demanda máxima (pico de importación)",
    response_model=ApiResponse[MaxDemandResult],
)
async def analytics_max_demand(
    repo: RepoDep,
    settings: SettingsDep,
    _user: CurrentUser,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[MaxDemandResult]:
    """Pico de potencia importada de la red en el periodo, con su timestamp."""
    start, stop = _resolve_range(settings, from_, to)
    return ApiResponse(data=await max_demand(repo, start, stop, device_id))


@router.get(
    "/load-factor",
    summary="Factor de carga de la importación",
    response_model=ApiResponse[LoadFactorResult],
)
async def analytics_load_factor(
    repo: RepoDep,
    settings: SettingsDep,
    _user: CurrentUser,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[LoadFactorResult]:
    """`promedio / pico` de la potencia importada (0..1); más cerca de 1 =
    demanda más plana. Solo considera ventanas de importación."""
    start, stop = _resolve_range(settings, from_, to)
    return ApiResponse(data=await load_factor(repo, start, stop, device_id))


@router.get(
    "/base-load", summary="Carga base aproximada", response_model=ApiResponse[BaseLoadResult]
)
async def analytics_base_load(
    repo: RepoDep,
    settings: SettingsDep,
    _user: CurrentUser,
    from_: FromQuery = None,
    to: ToQuery = None,
    percentile: Annotated[float, Query(gt=0, lt=1)] = DEFAULT_PERCENTILE,
    device_id: str | None = None,
) -> ApiResponse[BaseLoadResult]:
    """Percentil bajo (por defecto P10) de la potencia importada — proxy de
    la carga siempre encendida. No es consumo real aislado de la generación
    solar: el sistema no mide eso."""
    start, stop = _resolve_range(settings, from_, to)
    return ApiResponse(data=await base_load(repo, start, stop, device_id, percentile))


@router.get("/compare", summary="Comparar dos periodos", response_model=ApiResponse[CompareResult])
async def analytics_compare(
    repo: RepoDep,
    _user: CurrentUser,
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
