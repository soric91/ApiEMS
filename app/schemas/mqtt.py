"""Payload real del script de adquisición.

Tópico: gatewayems/modbus/{modbus_id}/{equipment_uuid}.
"""

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
    # Extraídos del tópico MQTT (no vienen en el JSON del payload), rellenados
    # por MQTTService tras el parseo. Confirmado por config real del script de
    # adquisición (sección `[Inversor_TCP] identify_device = <uuid>` por
    # equipo, no por gateway): el UUID del tópico es del EQUIPO, el mismo
    # valor que ya trae `identify_device` en el payload — no del gateway.
    # Se guardan para el cross-check en `mqtt/client.py` (detectar config
    # desalineada), no como fuente de identidad — esa sigue siendo
    # `identify_device`, ya confirmado como tag real en InfluxDB.
    equipment_uuid: str | None = None
    modbus_id: int | None = None
