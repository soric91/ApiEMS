"""Modelos del estado en tiempo real (RAM, alimentado por MQTT)."""

from datetime import datetime

from pydantic import BaseModel


class DeviceSnapshot(BaseModel):
    device_id: str
    device_name: str
    device_type: str
    identify_device: str
    timestamp: datetime
    received_at: datetime
    data: dict[str, float]
    # Del tópico MQTT (ver DeviceReading) — informativo, no reemplaza `device_id`
    # como identidad (esa sigue siendo la clave real hasta confirmar contra
    # qué tag usa InfluxDB en producción).
    gateway_uuid: str | None = None
    modbus_id_from_topic: int | None = None
