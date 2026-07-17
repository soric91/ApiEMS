"""Modelos de /history."""

from datetime import datetime

from pydantic import BaseModel

from app.models.variables import Aggregation, Variable
from app.schemas.influx import TimeSeriesPoint


class HistoryResponse(BaseModel):
    variable: Variable
    device_id: str | None
    aggregation: Aggregation
    period_start: datetime
    period_end: datetime
    interval_seconds: int
    points: list[TimeSeriesPoint]


class RangeSummary(BaseModel):
    variable: Variable
    device_id: str | None
    period_start: datetime
    period_end: datetime
    mean: float | None = None
    max: float | None = None
    min: float | None = None
    last: float | None = None
    total_kwh: float | None = None
