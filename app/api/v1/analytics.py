"""Analytics: perfiles horarios, comparación de periodos y energía reactiva.

Los indicadores de carga (max-demand, load-factor, base-load) ya no tienen
endpoint propio (fase V3): se calculan solo sobre POWER_ACTIVE_INST_TOTAL > 0
(importación de la red) y viven dentro de /reports/*. Acá quedan los perfiles,
las comparaciones y los cuadrantes reactivos.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentFleet
from app.dependencies.influx import get_influx_repository
from app.dependencies.tariff import get_tariff_config
from app.models.variables import REACTIVE_QUADRANTS
from app.repositories.influx import EnergyRecord
from app.repositories.scoped import ScopedInfluxRepository
from app.schemas.analytics import (
    AnalyticsSummary,
    CompareResult,
    HourProfilePoint,
    ReactiveQuadrantsResult,
    WeekdayProfilePoint,
)
from app.schemas.common import ApiResponse
from app.schemas.tariff import TariffConfig
from app.services.analytics.compare import compare_periods
from app.services.analytics.profile import daily_profile, weekday_profile
from app.services.analytics.reactive import reactive_quadrants
from app.services.analytics.summary import analytics_summary
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
    "/reactive-quadrants",
    summary="Cuadrantes de energía reactiva (kvarh)",
    response_model=ApiResponse[ReactiveQuadrantsResult],
)
async def analytics_reactive_quadrants(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> ApiResponse[ReactiveQuadrantsResult]:
    """Energía reactiva del período por cuadrante (kvarh): Q1/Q2 importada de
    la red (inductiva/capacitiva), Q3/Q4 exportada a la red, su balance y la
    tendencia por ventana. Por defecto: hoy."""
    bounds = _resolve_range(settings, from_, to)
    return ApiResponse(data=await reactive_quadrants(repo, bounds.start, bounds.stop, device_id))


@router.get(
    "/reactive-quadrants/csv",
    summary="Datos crudos (1 Hz) de los cuadrantes reactivos, en CSV",
)
async def analytics_reactive_quadrants_csv(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    device_id: str | None = None,
) -> StreamingResponse:
    """Todos los puntos reales de Q1..Q4 del rango, una fila por lectura.

    El cuerpo es un generador async que FastAPI consume pasa a pasa: el CSV se
    arma del Influx hacia la descarga, sin listas intermedias con cientos de
    miles de puntos. La columna `campo` lleva el nombre IEC del contador
    (Q1Eq..Q4Eq) y `fecha_hora_utc` la marca de tiempo con su offset, para que
    una hoja de cálculo no reinterprete el huso."""
    bounds = _resolve_range(settings, from_, to)
    records = await repo.energy_records(REACTIVE_QUADRANTS, bounds.start, bounds.stop, device_id)
    filename = f"reactiva_{bounds.start:%Y%m%dT%H%M}_{bounds.stop:%Y%m%dT%H%M}.csv"
    return StreamingResponse(
        _stream_reactive_csv(records),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _stream_reactive_csv(
    records: AsyncGenerator[EnergyRecord],
) -> AsyncGenerator[str]:
    """Vuelca cada punto crudo a una fila CSV, sin guardar nada en memoria."""

    yield "fecha_hora_utc,identify_device,campo,valor_kvarh\r\n"
    async for time, device_id, field, value in records:
        yield f"{time.isoformat()},{device_id},{field},{value:.2f}\r\n"


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
