"""Modelos de resúmenes de energía por periodo (consumidos por el servicio
`period_summary` y el cálculo de costos por preset)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.influx import EnergyPoint

Period = Literal["day", "week", "month", "year"]


class EnergySummary(BaseModel):
    period: Period
    device_id: str | None
    period_start: datetime
    period_end: datetime
    total_kwh: float
    series: list[EnergyPoint]
