"""Modelos de /history."""

from datetime import datetime

from pydantic import BaseModel

from app.models.variables import Aggregation, Variable
from app.schemas.influx import TimeSeriesPoint


class HistoryStats(BaseModel):
    """Mínimo, máximo, promedio y último valor de una variable instantánea en
    el rango — reducidos por InfluxDB sobre los datos crudos, NO sobre los
    puntos ya agregados de `/history`.

    La distinción importa: `/history` agrega por ventana (mean por defecto), y
    el máximo de unos promedios no es el máximo real. Con "agrupar cada 24 h",
    el "máximo" de la serie es el mayor de los promedios diarios, que puede ser
    varias veces menor que el pico verdadero.
    """

    variable: Variable
    device_id: str | None
    period_start: datetime
    period_end: datetime
    min: float | None
    max: float | None
    mean: float | None
    last: float | None


class HistoryResponse(BaseModel):
    variable: Variable
    device_id: str | None
    aggregation: Aggregation
    period_start: datetime
    period_end: datetime
    interval_seconds: int
    points: list[TimeSeriesPoint]
