"""Histórico consultado directo a InfluxDB.

/history            → serie con interval explícito.
/history/downsample  → serie con interval calculado para no exceder target_points.
/history/range       → resumen (sin ventana): mean/max/min/last (instantáneas)
                        o total_kwh (contadores).
"""

import asyncio
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies.auth import CurrentFleet
from app.dependencies.influx import get_influx_repository
from app.models.variables import Aggregation, Variable, is_cumulative
from app.repositories.scoped import ScopedInfluxRepository
from app.schemas.common import ApiResponse
from app.schemas.history import HistoryResponse, RangeSummary
from app.schemas.influx import TimeSeriesPoint

router = APIRouter(prefix="/history", tags=["History"])

RepoDep = Annotated[ScopedInfluxRepository, Depends(get_influx_repository)]
FromQuery = Annotated[datetime, Query(alias="from", description="Inicio del rango (UTC, ISO 8601)")]
ToQuery = Annotated[datetime, Query(description="Fin del rango (UTC, ISO 8601)")]

MIN_INTERVAL_SECONDS = 10
MAX_POINTS = 5000
DEFAULT_DOWNSAMPLE_TARGET_POINTS = 500


def _validate_range(from_: datetime, to: datetime) -> None:
    if from_ >= to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'from' debe ser anterior a 'to'")


def _check_point_budget(from_: datetime, to: datetime, interval_seconds: int) -> None:
    span = (to - from_).total_seconds()
    if span / interval_seconds > MAX_POINTS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"El rango solicitado excede {MAX_POINTS} puntos con ese interval_seconds; "
            "amplía el interval o reduce el rango (o usa /history/downsample).",
        )


async def _series(
    repo: ScopedInfluxRepository,
    variable: Variable,
    from_: datetime,
    to: datetime,
    interval_seconds: int,
    aggregation: Aggregation,
    device_id: str | None,
) -> HistoryResponse:
    every = timedelta(seconds=interval_seconds)
    if is_cumulative(variable):
        raw = await repo.energy_series(variable, from_, to, every, device_id)
        used_aggregation = Aggregation.LAST  # difference() ya aplicado; no es una reducción simple
    else:
        raw = await repo.instant_series(variable, from_, to, every, aggregation, device_id)
        used_aggregation = aggregation
    points = [TimeSeriesPoint(time=p.time, value=p.value) for p in raw]
    return HistoryResponse(
        variable=variable,
        device_id=device_id,
        aggregation=used_aggregation,
        period_start=from_,
        period_end=to,
        interval_seconds=interval_seconds,
        points=points,
    )


@router.get(
    "",
    summary="Serie histórica",
    response_model=ApiResponse[HistoryResponse],
    responses={400: {"description": "Rango inválido o demasiados puntos"}},
)
async def history(
    repo: RepoDep,
    fleet: CurrentFleet,
    variable: Variable,
    from_: FromQuery,
    to: ToQuery,
    interval_seconds: Annotated[
        int, Query(gt=0, description="Tamaño de ventana en segundos")
    ] = 900,
    device_id: str | None = None,
    aggregation: Aggregation = Aggregation.MEAN,
) -> ApiResponse[HistoryResponse]:
    """Serie temporal de una variable entre `from` y `to`.

    - Si `variable` es un contador acumulativo (`POWER_ACTIVE_TOTAL_POS` o
      `_NEG`), se aplica automáticamente `difference()` por ventana (energía
      en kWh) y se ignora `aggregation`.
    - Si es instantánea, se aplica `aggregation` (mean/max/min/last) por
      ventana de `interval_seconds`.
    """
    _validate_range(from_, to)
    _check_point_budget(from_, to, interval_seconds)
    return ApiResponse(
        data=await _series(repo, variable, from_, to, interval_seconds, aggregation, device_id)
    )


@router.get(
    "/downsample",
    summary="Serie histórica con interval automático",
    response_model=ApiResponse[HistoryResponse],
    responses={400: {"description": "Rango inválido"}},
)
async def history_downsample(
    repo: RepoDep,
    fleet: CurrentFleet,
    variable: Variable,
    from_: FromQuery,
    to: ToQuery,
    target_points: Annotated[int, Query(gt=0, le=MAX_POINTS)] = DEFAULT_DOWNSAMPLE_TARGET_POINTS,
    device_id: str | None = None,
    aggregation: Aggregation = Aggregation.MEAN,
) -> ApiResponse[HistoryResponse]:
    """Como `/history`, pero calcula automáticamente el `interval_seconds`
    para no exceder `target_points` en el rango solicitado — útil para
    graficar rangos largos (meses/años) sin pedir miles de puntos.
    """
    _validate_range(from_, to)
    span_seconds = (to - from_).total_seconds()
    interval_seconds = max(MIN_INTERVAL_SECONDS, int(span_seconds / target_points))
    return ApiResponse(
        data=await _series(repo, variable, from_, to, interval_seconds, aggregation, device_id)
    )


@router.get(
    "/range",
    summary="Resumen estadístico de un rango",
    response_model=ApiResponse[RangeSummary],
    responses={400: {"description": "Rango inválido"}},
)
async def history_range(
    repo: RepoDep,
    fleet: CurrentFleet,
    variable: Variable,
    from_: FromQuery,
    to: ToQuery,
    device_id: str | None = None,
) -> ApiResponse[RangeSummary]:
    """Resumen sin ventana temporal, calculado sobre todo el rango:

    - Variable instantánea → `mean`/`max`/`min`/`last`.
    - Contador acumulativo → `total_kwh` (`spread()` = last - first);
      `mean`/`max`/`min` quedan en `null` porque no aplican sobre contadores.
    """
    _validate_range(from_, to)
    if is_cumulative(variable):
        total = await repo.energy_total(variable, from_, to, device_id)
        summary = RangeSummary(
            variable=variable,
            device_id=device_id,
            period_start=from_,
            period_end=to,
            total_kwh=total,
        )
    else:
        mean, max_, min_, last = await asyncio.gather(
            repo.instant_reduce(variable, from_, to, Aggregation.MEAN, device_id),
            repo.instant_reduce(variable, from_, to, Aggregation.MAX, device_id),
            repo.instant_reduce(variable, from_, to, Aggregation.MIN, device_id),
            repo.instant_reduce(variable, from_, to, Aggregation.LAST, device_id),
        )
        summary = RangeSummary(
            variable=variable,
            device_id=device_id,
            period_start=from_,
            period_end=to,
            mean=mean,
            max=max_,
            min=min_,
            last=last,
        )
    return ApiResponse(data=summary)
