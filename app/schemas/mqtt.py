"""Payload real del script de adquisición (tópico gatewayems/modbus)."""

from datetime import datetime

from pydantic import BaseModel


class DeviceReading(BaseModel):
    device_name: str
    device_id: int
    identify_device: str
    device_type: str
    timestamp: datetime
    success: bool
    error: str | None = None
    data: dict[str, float]
