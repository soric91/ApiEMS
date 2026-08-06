"""Modelos del estado en tiempo real (RAM, alimentado por MQTT)."""

from datetime import datetime

from pydantic import BaseModel


class DeviceSnapshot(BaseModel):
    # = identify_device (UUID por equipo) — confirmado como tag real en
    # InfluxDB, único en toda la flota. Ver app/services/realtime/state.py.
    device_id: str
    device_name: str
    device_type: str
    identify_device: str
    timestamp: datetime
    received_at: datetime
    data: dict[str, float]
    # Del tópico MQTT — mismo valor que identify_device (cross-check en
    # mqtt/client.py), no una identidad aparte.
    equipment_uuid: str | None = None
    modbus_id: int | None = None
