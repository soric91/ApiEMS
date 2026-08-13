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
