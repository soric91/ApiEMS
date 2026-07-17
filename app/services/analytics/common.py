"""Utilidades compartidas por los servicios de analytics."""

from datetime import datetime, timedelta

import polars as pl

_TARGET_POINTS = 500
_MIN_INTERVAL = timedelta(minutes=1)


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
