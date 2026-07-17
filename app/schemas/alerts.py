"""Modelos de alertas de consumo anómalo.

Sin modelo de ML: se comparan lecturas contra bandas de percentiles (P10-P90)
calculadas sobre datos históricos reales — ver app/services/analytics/anomaly.py
para la justificación estadística.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

AlertSeverity = Literal["moderate", "high"]
AlertKind = Literal["hourly_power", "daily_total"]


class BandStats(BaseModel):
    """Banda esperada [p10, p90] para un bucket (hora del día o día de semana)."""

    p10: float
    p90: float
    sample_count: int


class Alert(BaseModel):
    kind: AlertKind
    severity: AlertSeverity
    device_id: str | None
    variable: str
    value: float
    expected_low: float
    expected_high: float
    bucket: int  # hora local (0-23) si kind=hourly_power; weekday (0=lunes) si daily_total
    timestamp: datetime
    message: str


class AlertsResponse(BaseModel):
    recent: list[Alert]
    daily_total: Alert | None
