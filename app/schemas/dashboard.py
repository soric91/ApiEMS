"""Modelos del panel principal."""

from datetime import datetime

from pydantic import BaseModel


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
