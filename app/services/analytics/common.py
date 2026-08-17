"""Utilidades compartidas por los servicios de analytics."""

from datetime import datetime, timedelta

import polars as pl

from app.schemas.analytics import (
    BaseLoadResult,
    LoadFactorResult,
    MaxDemandResult,
)
from app.schemas.influx import TimeSeriesPoint

_TARGET_POINTS = 500
_MIN_INTERVAL = timedelta(minutes=1)

# Percentil de la carga base: el 10% más bajo de la importación del periodo.
# Vive acá, junto a `base_load_result`, y no en un módulo propio: es el único
# parámetro del cálculo y lo comparte quien arma el reporte.
DEFAULT_PERCENTILE = 0.10


def auto_interval(start: datetime, stop: datetime) -> timedelta:
    """Ventana de agregación que no excede ~500 puntos en el rango dado."""
    span = stop - start
    interval = span / _TARGET_POINTS
    return max(interval, _MIN_INTERVAL)


# Los stubs de Polars devuelven tipos muy amplios (PythonLiteral | None) para
# las reducciones de Series — se aíslan aquí, igual que _query() en
# InfluxRepository aísla los tipos imprecisos de influxdb-client.
def series_mean(series: pl.Series) -> float:
    return float(series.mean())  # pyright: ignore[reportArgumentType, reportUnknownMemberType]


def series_max(series: pl.Series) -> float:
    return float(series.max())  # pyright: ignore[reportArgumentType, reportUnknownMemberType]


def series_min(series: pl.Series) -> float:
    return float(series.min())  # pyright: ignore[reportArgumentType, reportUnknownMemberType]


def series_quantile(series: pl.Series, quantile: float) -> float | None:
    value = series.quantile(  # pyright: ignore[reportUnknownMemberType]
        quantile, interpolation="linear"
    )
    return None if value is None else float(value)  # pyright: ignore[reportArgumentType]


def power_frames(points: list[TimeSeriesPoint]) -> tuple[pl.DataFrame, pl.DataFrame | None]:
    """Dos marcos desde los puntos de potencia neta: el de TODAS las muestras
    (para p. ej. el promedio del KPIs) y el de solo importación (> 0, el que
    usan demanda máxima, factor de carga y carga base).

    El compartirlos acá es lo que permite que un reporte construya el
    DataFrame UNA sola vez y derive los tres indicadores de carga de ahí en
    lugar de releer la misma serie (ver `ReportBuilder.consolidate`).
    """
    if not points:
        return pl.DataFrame(), None
    full = pl.DataFrame({"time": [p.time for p in points], "value": [p.value for p in points]})
    importing: pl.DataFrame = full.filter(pl.col("value") > 0)  # pyright: ignore[reportUnknownMemberType]
    return full, importing


def _value_series(df: pl.DataFrame) -> pl.Series:
    return df["value"]  # pyright: ignore[reportUnknownMemberType]


def max_demand_result(
    start: datetime,
    stop: datetime,
    device_id: str | None,
    importing: pl.DataFrame | None,
) -> MaxDemandResult:
    """La demanda pico del periodo, a partir del marco de importación.

    `importing` tiene que venir de una serie agregada con `max()` por ventana,
    NO con `mean()`: promediar la ventana borra el pico. Con el intervalo
    automático (~500 puntos) un rango de 30 días usa ventanas de ~86 min, así
    que un pico de tres minutos desaparecía dentro del promedio y la demanda
    máxima salía varias veces más baja que la real. Ver
    `reports.builder.consolidate`, que lee las dos series (mean para promedios,
    max para el pico).

    `peak_at` es el INICIO de la ventana donde ocurrió el pico, no el instante
    exacto: la precisión del timestamp es la del bucket, la del valor no.
    """
    empty = MaxDemandResult(
        period_start=start, period_end=stop, device_id=device_id, peak_power_w=None, peak_at=None
    )
    if importing is None or importing.is_empty():
        return empty
    sorted_df: pl.DataFrame = importing.sort("value", descending=True)  # pyright: ignore[reportUnknownMemberType]
    return MaxDemandResult(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        peak_power_w=round(float(sorted_df["value"][0]), 2),  # pyright: ignore[reportIndexIssue]
        peak_at=sorted_df["time"][0],
    )


def load_factor_result(
    start: datetime,
    stop: datetime,
    device_id: str | None,
    importing: pl.DataFrame | None,
    peak_w: float | None = None,
) -> LoadFactorResult:
    """Factor de carga = importación media / demanda pico.

    `peak_w` es la demanda pico REAL (la que calcula `max_demand_result` sobre
    la serie agregada con `max()`). Sin ella el pico se toma del mismo marco
    promediado que la media, que lo subestima y por tanto INFLA el factor de
    carga — justo el error que hacía ver instalaciones mucho más parejas de lo
    que son. El promedio sí sale del marco promediado: ahí `mean()` es correcto.
    """
    empty = LoadFactorResult(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        average_import_w=None,
        peak_import_w=None,
        load_factor=None,
    )
    if importing is None or importing.is_empty():
        return empty
    values = _value_series(importing)
    avg = series_mean(values)
    peak = peak_w if peak_w is not None else series_max(values)
    return LoadFactorResult(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        average_import_w=round(avg, 2),
        peak_import_w=round(peak, 2),
        load_factor=round(avg / peak, 4) if peak > 0 else None,
    )


def base_load_result(
    start: datetime,
    stop: datetime,
    device_id: str | None,
    importing: pl.DataFrame | None,
    percentile: float,
) -> BaseLoadResult:
    empty = BaseLoadResult(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        percentile=percentile,
        base_load_w=None,
    )
    if importing is None or importing.is_empty():
        return empty
    value = series_quantile(_value_series(importing), percentile)
    if value is None:
        return empty
    return BaseLoadResult(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        percentile=percentile,
        base_load_w=round(value, 2),
    )
