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
    BaseLoadTrendResult,
    CompareResult,
    CoverageResult,
    DayArchetypesResult,
    HeatmapResult,
    HourProfilePoint,
    LoadDurationResult,
    ReactiveQuadrantsResult,
    SiteModeResult,
    WeekdayProfilePoint,
)
from app.schemas.common import ApiResponse
from app.schemas.tariff import TariffConfig
from app.services.analytics.archetypes import DEFAULT_DAYS as DEFAULT_ARCHETYPE_DAYS
from app.services.analytics.archetypes import day_archetypes
from app.services.analytics.baseload import DEFAULT_PERCENTILE as DEFAULT_BASELOAD_PERCENTILE
from app.services.analytics.baseload import baseload_trend
from app.services.analytics.compare import compare_periods
from app.services.analytics.coverage import coverage
from app.services.analytics.heatmap import HeatmapMetric, heatmap
from app.services.analytics.load_duration import DEFAULT_POINTS as DEFAULT_DURATION_POINTS
from app.services.analytics.load_duration import load_duration
from app.services.analytics.profile import daily_profile, weekday_profile
from app.services.analytics.reactive import reactive_quadrants
from app.services.analytics.site_mode import resolve_site_mode
from app.services.analytics.summary import analytics_summary
from app.services.crm.fleet import ClientFleet
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


def _declaraciones(fleet: ClientFleet, device_id: str | None) -> list[bool | None]:
    """Lo que el CRM declara sobre la generación de las sedes consultadas."""
    return [
        device.tiene_generacion
        for device in fleet.devices
        if device_id is None or device.id == device_id
    ]


def _reading_interval(fleet: ClientFleet, device_id: str | None) -> int | None:
    """Cada cuánto publica el equipo consultado, según el CRM.

    Sin `device_id` la consulta agrega varios equipos: solo se devuelve un
    intervalo si TODOS declaran el mismo. Con dos gateways a ritmos distintos,
    cualquier número sería la cobertura de uno y el error del otro, así que se
    deja que se infiera de los datos."""
    intervalos = {
        device.intervalo_lectura_segundos
        for device in fleet.devices
        if device_id is None or device.id == device_id
    }
    if len(intervalos) != 1:
        return None
    return intervalos.pop()


def _resolve_range(settings: Settings, from_: datetime | None, to: datetime | None) -> PeriodBounds:
    """Rango del endpoint por defecto (hoy) con las sobreescrituras from/to."""
    try:
        return resolve_period("day", settings.TIMEZONE, from_=from_, to=to)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get(
    "/day-archetypes",
    summary="Los tipos de día de la instalación",
    response_model=ApiResponse[DayArchetypesResult],
)
async def analytics_day_archetypes(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    days: Annotated[int, Query(gt=0, le=365)] = DEFAULT_ARCHETYPE_DAYS,
    device_id: str | None = None,
) -> ApiResponse[DayArchetypesResult]:
    """Agrupa los días por la FORMA de su consumo horario.

    El perfil horario promedio mezcla el laboral, el domingo y el día del
    asado en una curva que no describe a ninguno. Acá cada tipo de día se
    muestra por separado — "tienes tres tipos de día" es una frase que el
    cliente reconoce en su propia vida.

    Si los grupos no se separan lo suficiente, `archetypes` viene vacío con la
    silueta obtenida: esta instalación consume igual todos los días y decir lo
    contrario sería inventar una frontera.
    """
    return ApiResponse(
        data=await day_archetypes(repo, device_id, settings.TIMEZONE, days)
    )


@router.get(
    "/load-duration",
    summary="Curva de duración de carga",
    response_model=ApiResponse[LoadDurationResult],
)
async def analytics_load_duration(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    points: Annotated[int, Query(gt=1, le=1000)] = DEFAULT_DURATION_POINTS,
    device_id: str | None = None,
) -> ApiResponse[LoadDurationResult]:
    """La potencia importada ordenada de mayor a menor contra el % del tiempo.

    Contesta si el consumo es parejo o vive de picos: "el 5% del tiempo estás
    por encima de 4,2 kW, y ese 5% explica el 22% de tu energía". Es lo que
    decide si conviene atacar los picos o el consumo de fondo.

    Se devuelven `points` muestras de la curva, no las miles de lecturas: el
    dibujo no cambia y la respuesta no pesa.
    """
    bounds = _resolve_range(settings, from_, to)
    return ApiResponse(
        data=await load_duration(repo, bounds.start, bounds.stop, device_id, points)
    )


@router.get(
    "/baseload-trend",
    summary="Carga base día a día y lo que cuesta al mes",
    response_model=ApiResponse[BaseLoadTrendResult],
)
async def analytics_baseload_trend(
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    percentile: Annotated[float, Query(gt=0, lt=1)] = DEFAULT_BASELOAD_PERCENTILE,
    device_id: str | None = None,
) -> ApiResponse[BaseLoadTrendResult]:
    """El consumo de fondo que nunca baja, su tendencia y su costo mensual.

    Es la cifra que más veces se traduce en dinero sin cambiar hábitos: 180 W
    constantes son ~130 kWh al mes que se pagan aunque no haya nadie.

    En una sede sin generación se mide sobre el día completo; con generación,
    solo en la ventana nocturna — de día el medidor ve el balance neto y la
    fotovoltaica tapa el consumo real.
    """
    bounds = _resolve_range(settings, from_, to)
    mode, _ = await resolve_site_mode(repo, _declaraciones(fleet, device_id), device_id)
    return ApiResponse(
        data=await baseload_trend(
            repo,
            bounds.start,
            bounds.stop,
            device_id,
            settings.TIMEZONE,
            mode,
            tariff,
            percentile,
        )
    )


@router.get(
    "/heatmap",
    summary="Mapa de calor hora x día",
    response_model=ApiResponse[HeatmapResult],
)
async def analytics_heatmap(
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    metric: HeatmapMetric = "import",
    device_id: str | None = None,
) -> ApiResponse[HeatmapResult]:
    """La energía del rango reordenada en una cuadrícula de 24 horas x N días.

    Es la vista donde saltan los patrones que una línea esconde: la hora en que
    siempre se dispara el consumo, el fin de semana que se comporta distinto,
    el día que se salió de lo normal.

    `metric`: `import`/`export` (kWh de esa hora), `net` (importado menos
    exportado) o `cost` (lo que costó la importación de esa hora, con la tarifa
    del mes que corresponda).
    """
    bounds = _resolve_range(settings, from_, to)
    return ApiResponse(
        data=await heatmap(
            repo, bounds.start, bounds.stop, device_id, metric, tariff, settings.TIMEZONE
        )
    )


@router.get(
    "/coverage",
    summary="Cuánto dato hay realmente en el rango",
    response_model=ApiResponse[CoverageResult],
)
async def analytics_coverage(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    from_: FromQuery = None,
    to: ToQuery = None,
    bucket_seconds: Annotated[
        int, Query(gt=0, description="Tamaño de la ventana en segundos")
    ] = 3600,
    device_id: str | None = None,
) -> ApiResponse[CoverageResult]:
    """Qué porcentaje de las lecturas esperadas llegó, ventana por ventana.

    Un hueco de datos no es consumo cero, pero se ve igual: un gateway caído
    diez horas deja un día que parece de bajo consumo. Acá se ve dónde faltan
    lecturas, y el panel puede marcar esos tramos en vez de dibujarlos como si
    fueran datos buenos.

    Las muestras esperadas salen del intervalo de lectura configurado en el CRM
    para el gateway; si no está, se infieren del propio rango y la respuesta lo
    dice en `expected_source`.
    """
    bounds = _resolve_range(settings, from_, to)
    return ApiResponse(
        data=await coverage(
            repo,
            bounds.start,
            bounds.stop,
            timedelta(seconds=bucket_seconds),
            device_id,
            _reading_interval(fleet, device_id),
        )
    )


@router.get(
    "/site-mode",
    summary="Si la sede tiene generación propia o es de consumo puro",
    response_model=ApiResponse[SiteModeResult],
)
async def analytics_site_mode(
    repo: RepoDep,
    fleet: CurrentFleet,
    device_id: str | None = None,
) -> ApiResponse[SiteModeResult]:
    """Cómo hay que leer el medidor de frontera de esta sede.

    Con generación fotovoltaica el medidor solo ve el BALANCE NETO, así que en
    horas de sol el consumo real queda escondido y varios indicadores solo
    valen de noche. Sin generación, todo lo que pasa por el medidor es consumo
    y valen las 24 h. El panel usa esto para no mostrar exportación, saldo a
    favor ni balance neto en una instalación que nunca va a tener ninguno.

    Manda lo declarado en el CRM; si nadie lo declaró, se deduce de la energía
    exportada del último mes (cacheado 24 h: una sede no cambia de modo
    intradía).
    """
    mode, source = await resolve_site_mode(repo, _declaraciones(fleet, device_id), device_id)
    return ApiResponse(data=SiteModeResult(device_id=device_id, mode=mode, source=source))


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
