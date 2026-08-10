"""Modelos del panel principal."""

from datetime import datetime

from pydantic import BaseModel

from app.schemas.kpis import KpiSummary
from app.schemas.tariff import CostBreakdown


class DashboardData(BaseModel):
    device_id: str
    power_active_total_w: float
    voltage_a: float
    voltage_b: float
    current_a: float
    current_b: float
    power_factor: float
    consumption_today_kwh: float
    consumption_month_kwh: float
    export_today_kwh: float
    export_month_kwh: float
    last_update: datetime


class DashboardCard(BaseModel):
    key: str
    label: str
    value: float
    unit: str


class DashboardStatus(BaseModel):
    mqtt_connected: bool
    influx_connected: bool
    devices_online: int
    devices_total: int
    last_message_at: datetime | None


class DashboardSummary(BaseModel):
    """Payload consolidado del panel, en una sola llamada.

    El frontend (Fase 5) moverá el arranque del dashboard a este endpoint:
    snapshots en vivo (RAM) + energía de hoy/al mes + costos + KPIs, todo
    saliendo de las mismas series cacheadas que los endpoints individuales.
    La conectividad NO va aquí: ya la expone /dashboard/status.
    """

    device_id: str
    last_update: datetime
    # En vivo (RAM, vía MQTT)
    power_active_total_w: float
    voltage_a: float
    voltage_b: float
    current_a: float
    current_b: float
    power_factor: float
    # Energía del periodo (InfluxDB, cacheada)
    consumption_today_kwh: float
    consumption_month_kwh: float
    export_today_kwh: float
    export_month_kwh: float
    # Costos del día y del mes (mismos rangos que /costs/*)
    costs_day: CostBreakdown
    costs_month: CostBreakdown
    # KPIs del día (idénticos a /kpis con el default de hoy)
    kpis: KpiSummary
