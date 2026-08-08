"""Costo/crédito en COP: consumo importado por su tarifa, exportación como
crédito (dos tramos — ver app/services/tariff/cost.py). Deriva de
/consumption, /export y la tarifa configurada — no es una medición nueva,
es aritmética sobre kWh ya calculados."""

import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentFleet
from app.dependencies.influx import get_influx_repository
from app.dependencies.tariff import get_tariff_config
from app.models.variables import Variable
from app.repositories.scoped import ScopedInfluxRepository
from app.schemas.common import ApiResponse
from app.schemas.energy import Period
from app.schemas.tariff import CostBreakdown, TariffConfig
from app.services.analytics.common import auto_interval
from app.services.energy.summary import period_summary
from app.services.influx.cache import cached_energy_series, cached_energy_total
from app.services.tariff.cost import compute_cost, compute_cost_from_points

router = APIRouter(prefix="/costs", tags=["Costs"])

RepoDep = Annotated[ScopedInfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
TariffDep = Annotated[TariffConfig, Depends(get_tariff_config)]
FromQuery = Annotated[datetime, Query(alias="from", description="Inicio del rango (UTC, ISO 8601)")]
ToQuery = Annotated[datetime, Query(description="Fin del rango (UTC, ISO 8601)")]


async def _cost_for_period(
    period: Period,
    repo: ScopedInfluxRepository,
    settings: Settings,
    tariff: TariffConfig,
    device_id: str | None,
) -> ApiResponse[CostBreakdown]:
    consumption, export = await asyncio.gather(
        period_summary(repo, Variable.POWER_ACTIVE_TOTAL_POS, period, settings.TIMEZONE, device_id),
        period_summary(repo, Variable.POWER_ACTIVE_TOTAL_NEG, period, settings.TIMEZONE, device_id),
    )
    return ApiResponse(data=compute_cost(tariff, period, consumption, export, device_id))


@router.get(
    "/day",
    summary="Costo de hoy",
    description="Costo del día (importación x tarifa, menos crédito de exportación).",
    response_model=ApiResponse[CostBreakdown],
    deprecated=True,  # usar /reports/daily — mismo CostBreakdown, ya cacheado
)
async def day(  # pyright: ignore[reportUnusedFunction]
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    device_id: str | None = None,
) -> ApiResponse[CostBreakdown]:
    return await _cost_for_period("day", repo, settings, tariff, device_id)


@router.get(
    "/week",
    summary="Costo de la semana",
    description="Costo de la semana en curso.",
    response_model=ApiResponse[CostBreakdown],
    deprecated=True,  # usar /reports/weekly
)
async def week(  # pyright: ignore[reportUnusedFunction]
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    device_id: str | None = None,
) -> ApiResponse[CostBreakdown]:
    return await _cost_for_period("week", repo, settings, tariff, device_id)


@router.get(
    "/month",
    summary="Costo del mes",
    description="Costo del mes en curso.",
    response_model=ApiResponse[CostBreakdown],
    deprecated=True,  # usar /reports/monthly
)
async def month(  # pyright: ignore[reportUnusedFunction]
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    device_id: str | None = None,
) -> ApiResponse[CostBreakdown]:
    return await _cost_for_period("month", repo, settings, tariff, device_id)


@router.get(
    "/year",
    summary="Costo del año",
    description="Costo del año en curso. Usa la tarifa vigente de CADA mes, no la actual.",
    response_model=ApiResponse[CostBreakdown],
    deprecated=True,  # usar /reports/yearly
)
async def year(  # pyright: ignore[reportUnusedFunction]
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    device_id: str | None = None,
) -> ApiResponse[CostBreakdown]:
    return await _cost_for_period("year", repo, settings, tariff, device_id)


@router.get(
    "/range",
    summary="Costo de un rango arbitrario",
    description="Costo entre `from` y `to` (rango libre, ej. para Analytics o comparaciones).",
    response_model=ApiResponse[CostBreakdown],
    responses={400: {"description": "Rango inválido"}},
)
async def cost_range(  # pyright: ignore[reportUnusedFunction]
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    from_: FromQuery,
    to: ToQuery,
    device_id: str | None = None,
) -> ApiResponse[CostBreakdown]:
    if from_ >= to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'from' debe ser anterior a 'to'")

    every = auto_interval(from_, to)
    consumption_series, export_series, consumption_total, export_total = await asyncio.gather(
        cached_energy_series(repo, Variable.POWER_ACTIVE_TOTAL_POS, from_, to, every, device_id),
        cached_energy_series(repo, Variable.POWER_ACTIVE_TOTAL_NEG, from_, to, every, device_id),
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_POS, from_, to, device_id),
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_NEG, from_, to, device_id),
    )
    breakdown = compute_cost_from_points(
        tariff,
        "custom",
        from_,
        to,
        device_id,
        consumption_series,
        export_series,
        consumption_total,
        export_total,
    )
    return ApiResponse(data=breakdown)
