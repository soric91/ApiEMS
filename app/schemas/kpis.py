"""Modelos de /kpis."""

from datetime import datetime

from pydantic import BaseModel


class KpiSummary(BaseModel):
    period_start: datetime
    period_end: datetime
    device_id: str | None
    power_avg_w: float | None
    power_max_w: float | None
    voltage_avg_v: float | None
    voltage_min_v: float | None
    voltage_max_v: float | None
    current_avg_a: float | None
    power_factor_avg: float | None
    consumption_daily_kwh: float
    consumption_weekly_kwh: float
    consumption_monthly_kwh: float
    export_daily_kwh: float
    export_monthly_kwh: float
