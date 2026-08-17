"""Carga base ("siempre encendido"): el consumo de fondo que nunca baja.

Es la cifra que más veces se traduce en dinero sin cambiar de hábitos: 180 W
constantes son ~130 kWh al mes, y esos kWh se pagan las 24 horas de los 30
días aunque no haya nadie en la instalación.

Se calcula como el percentil 5 de la potencia importada de cada día, no como
el mínimo: un mínimo instantáneo lo fija cualquier hueco de medio segundo
entre dos ciclos de una nevera, y eso no es el consumo de fondo.

La ventana depende de cómo se lee el medidor:

- Sin generación (`consumo`) — el día COMPLETO. Todo lo que pasa por el
  medidor es consumo, así que la madrugada y la tarde valen igual.
- Con generación (`generacion`) — solo la ventana nocturna. De día el medidor
  ve el balance neto y la fotovoltaica tapa el consumo real: el percentil 5
  daría un número negativo, que como "carga base" no significa nada.

La tendencia (mediana de los últimos 7 días contra los 7 anteriores) es lo que
detecta que algo se quedó encendido: la carga base sube de golpe y ya no baja.
"""

import asyncio
from datetime import datetime, timedelta

import polars as pl

from app.models.variables import Aggregation, Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import BaseLoadTrendPoint, BaseLoadTrendResult, SiteMode
from app.schemas.influx import TimeSeriesPoint
from app.schemas.tariff import TariffConfig
from app.services.influx.cache import cached_energy_total, cached_instant_series
from app.services.tariff.cost import rate_for_month

DEFAULT_PERCENTILE = 0.05
# Ventana nocturna local para sedes con generación: entre medianoche y las 5
# nunca hay sol en ninguna época del año.
NIGHT_START_HOUR = 0
NIGHT_END_HOUR = 5
# Cada cuánto se muestrea la potencia. Quince minutos con `min` da el piso de
# cada tramo sin traer 96 mil puntos por mes.
_SAMPLE = timedelta(minutes=15)
_HOURS_PER_DAY = 24
_DAYS_PER_MONTH = 30
_TREND_DAYS = 7


def _month_key(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _daily_baseload(
    points: list[TimeSeriesPoint],
    tz_name: str,
    percentile: float,
    solo_noche: bool,
) -> list[BaseLoadTrendPoint]:
    """Percentil de la potencia importada por día local — cálculo puro (Polars)."""
    frame = pl.DataFrame(
        {"time": [p.time for p in points], "value": [p.value for p in points]},
        schema={"time": pl.Datetime(time_zone="UTC"), "value": pl.Float64},
    )
    local = frame.with_columns(  # pyright: ignore[reportUnknownMemberType]
        pl.col("time").dt.convert_time_zone(tz_name).dt.date().alias("fecha"),
        pl.col("time").dt.convert_time_zone(tz_name).dt.hour().alias("hora"),
    )
    # Solo importación: una muestra negativa es la casa exportando, no carga.
    local = local.filter(pl.col("value") > 0)  # pyright: ignore[reportUnknownMemberType]
    if solo_noche:
        local = local.filter(  # pyright: ignore[reportUnknownMemberType]
            (pl.col("hora") >= NIGHT_START_HOUR) & (pl.col("hora") < NIGHT_END_HOUR)
        )
    if local.is_empty():
        return []

    agrupado: pl.DataFrame = (
        local.group_by("fecha")  # pyright: ignore[reportUnknownMemberType]
        .agg(  # pyright: ignore[reportUnknownMemberType]
            # `lower` y no `linear`: devuelve una potencia REALMENTE observada
            # en vez de un promedio entre dos muestras lejanas. La carga base
            # es "el piso que se vio", y ese número se multiplica por 720 horas
            # para hablar de dinero — mejor que sea un dato y no una
            # interpolación.
            pl.col("value").quantile(percentile, interpolation="lower").alias("base"),
            pl.col("value").count().alias("muestras"),
        )
        .sort("fecha")  # pyright: ignore[reportUnknownMemberType]
    )
    return [
        BaseLoadTrendPoint(
            date=str(row["fecha"]),
            base_load_w=round(float(row["base"]), 2),
            sample_count=int(row["muestras"]),
        )
        for row in agrupado.iter_rows(named=True)
        if row["base"] is not None
    ]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordenados = sorted(values)
    medio = len(ordenados) // 2
    if len(ordenados) % 2 == 1:
        return ordenados[medio]
    return (ordenados[medio - 1] + ordenados[medio]) / 2


async def baseload_trend(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
    tz_name: str,
    site_mode: SiteMode,
    tariff: TariffConfig,
    percentile: float = DEFAULT_PERCENTILE,
) -> BaseLoadTrendResult:
    """La carga base día a día, y cuánto cuesta al mes."""
    solo_noche = site_mode == "generacion"
    points, import_total = await asyncio.gather(
        cached_instant_series(
            repo,
            Variable.POWER_ACTIVE_INST_TOTAL,
            start,
            stop,
            _SAMPLE,
            Aggregation.MIN,
            device_id,
        ),
        cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_POS, start, stop, device_id),
    )

    puntos = (
        await asyncio.to_thread(_daily_baseload, points, tz_name, percentile, solo_noche)
        if points
        else []
    )
    if not puntos:
        return BaseLoadTrendResult(
            device_id=device_id,
            period_start=start,
            period_end=stop,
            percentile=percentile,
            window="noche" if solo_noche else "dia",
            points=[],
            current_w=None,
            trend_delta_w=None,
            monthly_kwh=None,
            monthly_cost_cop=None,
            share_of_import=None,
        )

    valores = [p.base_load_w for p in puntos]
    current = _median(valores[-_TREND_DAYS:])
    previos = _median(valores[-2 * _TREND_DAYS : -_TREND_DAYS])
    trend = None if current is None or previos is None else round(current - previos, 2)

    monthly_kwh = None
    monthly_cost = None
    if current is not None:
        monthly_kwh = round(current * _HOURS_PER_DAY * _DAYS_PER_MONTH / 1000, 2)
        rate, _ = rate_for_month(tariff, _month_key(stop))
        if rate is not None:
            monthly_cost = round(monthly_kwh * rate.cu_cop_kwh, 2)

    # Qué parte de lo importado en el rango se fue en carga base. Se compara
    # contra las MISMAS horas que se midieron: con generación solo se observa
    # la noche, y estirar ese piso a las 24 h para compararlo con el total del
    # rango exageraría la porción.
    dias = len(puntos)
    horas_observadas = (NIGHT_END_HOUR - NIGHT_START_HOUR) if solo_noche else _HOURS_PER_DAY
    share = None
    if current is not None and import_total > 0:
        base_kwh = current * horas_observadas * dias / 1000
        share = round(min(1.0, base_kwh / import_total), 4)

    return BaseLoadTrendResult(
        device_id=device_id,
        period_start=start,
        period_end=stop,
        percentile=percentile,
        window="noche" if solo_noche else "dia",
        points=puntos,
        current_w=current,
        trend_delta_w=trend,
        monthly_kwh=monthly_kwh,
        monthly_cost_cop=monthly_cost,
        share_of_import=share,
    )
