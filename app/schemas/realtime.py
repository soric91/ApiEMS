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
