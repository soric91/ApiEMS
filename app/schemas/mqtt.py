"""Payload real del script de adquisición (tópico gatewayems/modbus/{modbus_id}/{gateway_uuid})."""

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
    # por MQTTService tras el parseo. `gateway_uuid` es el mismo UUID que usa
    # CRMBackend para identificar el gateway (`Gateway.uuid`) — a diferencia
    # de `device_id` (un entero de bus Modbus, único solo dentro de UN
    # gateway), es estable y globalmente único entre gateways.
    #
    # Deliberadamente NO se usa todavía como identidad en RealtimeState/
    # alerts/websocket: esas rutas correlacionan contra InfluxDB con
    # `str(device_id)`, y el tag que InfluxDB realmente usa lo escribe el
    # script de adquisición (fuera de este repo) — no hay forma de confirmar
    # desde acá que coincide con `gateway_uuid` sin verlo en producción.
    # Recablear la identidad sin esa confirmación arriesga romper en
    # silencio la correlación de `check_hourly` (las bandas históricas
    # dejarían de encontrar datos). Quedan disponibles para quien los
    # necesite (ej. mostrar el gateway en el selector de medidores).
    gateway_uuid: str | None = None
    modbus_id_from_topic: int | None = None
