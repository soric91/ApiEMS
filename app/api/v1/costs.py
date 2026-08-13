"""Costo/crédito en COP: consumo importado por su tarifa, exportación como
crédito (dos tramos — ver app/services/tariff/cost.py). Deriva de las series
de energía y la tarifa configurada — no es una medición nueva, es aritmética
sobre kWh ya calculados. El contrato vigente es /costs/range; los costos de
periodos fijos viven en /reports/{daily,weekly,monthly,yearly}.
"""

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
from app.schemas.tariff import CostBreakdown, TariffConfig
from app.services.analytics.common import auto_interval
from app.services.influx.cache import cached_energy_series, cached_energy_total
from app.services.tariff.cost import compute_cost_from_points

router = APIRouter(prefix="/costs", tags=["Costs"])

RepoDep = Annotated[ScopedInfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
TariffDep = Annotated[TariffConfig, Depends(get_tariff_config)]
FromQuery = Annotated[datetime, Query(alias="from", description="Inicio del rango (UTC, ISO 8601)")]
ToQuery = Annotated[datetime, Query(description="Fin del rango (UTC, ISO 8601)")]


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
