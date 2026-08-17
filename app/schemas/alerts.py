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


class LevelShift(BaseModel):
    """Un cambio de nivel sostenido en el consumo diario.

    Distinto de una anomalía puntual: acá cada día por separado puede caer
    dentro de lo normal mientras el PROMEDIO se corrió y se quedó ahí — una
    nevera que se degrada, un termo mal configurado, un equipo nuevo.
    """

    detected_at: datetime
    before_kwh: float
    after_kwh: float
    delta_pct: float
    direction: Literal["up", "down"]
    message: str


class AlertsHistory(BaseModel):
    """Qué días del rango se salieron de lo normal y desde cuándo cambió el
    nivel de consumo.

    Se recalcula sobre los datos guardados, no se lee de una tabla de eventos:
    la energía diaria y su banda ya están en InfluxDB, y guardar además el
    veredicto sería un segundo origen de verdad que puede contradecir al
    primero (ver `app/services/alerts/history.py`).
    """

    device_id: str | None
    period_start: datetime
    period_end: datetime
    days_analyzed: int
    anomalies: list[Alert]
    level_shift: LevelShift | None
