"""Último valor por dispositivo, en memoria.

El tiempo real NUNCA consulta InfluxDB: cada mensaje MQTT actualiza este
estado y de aquí leen el WebSocket y los endpoints /realtime.
Sin locks: FastAPI corre en un solo event loop y update() es síncrono.
"""

from datetime import UTC, datetime

from app.schemas.mqtt import DeviceReading
from app.schemas.realtime import DeviceSnapshot


class RealtimeState:
    def __init__(self) -> None:
        self._devices: dict[str, DeviceSnapshot] = {}

    def update(self, reading: DeviceReading) -> DeviceSnapshot:
        snapshot = DeviceSnapshot(
            device_id=str(reading.device_id),
            device_name=reading.device_name,
            device_type=reading.device_type,
            identify_device=reading.identify_device,
            timestamp=reading.timestamp,
            received_at=datetime.now(tz=UTC),
            data=reading.data,
            gateway_uuid=reading.gateway_uuid,
            modbus_id_from_topic=reading.modbus_id_from_topic,
        )
        self._devices[snapshot.device_id] = snapshot
        return snapshot

    def latest(self) -> list[DeviceSnapshot]:
        return list(self._devices.values())

    def device(self, device_id: str) -> DeviceSnapshot | None:
        return self._devices.get(device_id)

    def values_of(self, variable: str) -> list[DeviceSnapshot]:
        """Snapshots de los dispositivos que reportan la variable dada."""
        return [snap for snap in self._devices.values() if variable in snap.data]
