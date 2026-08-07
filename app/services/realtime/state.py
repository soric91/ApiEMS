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
        # Identidad = identify_device (UUID por equipo, único en toda la
        # flota — confirmado como tag real en InfluxDB, ver
        # app/repositories/influx.py). El `device_id` entero del payload solo
        # es único DENTRO de un gateway/bus: dos gateways distintos pueden
        # reportar el mismo entero y colisionarían en este diccionario.
        snapshot = DeviceSnapshot(
            device_id=reading.identify_device,
            device_name=reading.device_name,
            device_type=reading.device_type,
            identify_device=reading.identify_device,
            timestamp=reading.timestamp,
            received_at=datetime.now(tz=UTC),
            data=reading.data,
            equipment_uuid=reading.equipment_uuid,
            modbus_id=reading.modbus_id,
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
