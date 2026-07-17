from dataclasses import dataclass

from app.core.config import Settings
from app.schemas.mqtt import DeviceReading
from app.services.mqtt.client import MQTTService

# Payload real capturado con mosquitto_sub del tópico gatewayems/modbus
REAL_PAYLOAD = b"""{
  "device_name": "Modbus_DTSU666_11",
  "device_id": 11,
  "identify_device": "bf6a469f-4c2a-4402-9438-49a491ad2238",
  "timestamp": "2026-07-16 13:26:00.467611+00:00",
  "data": {
    "VOLTAGE_A": 120.4, "VOLTAGE_B": 121.2,
    "CURRENT_A": 1.93, "CURRENT_B": 2.81,
    "POWER_ACTIVE_INST_TOTAL": -442.2,
    "POWER_ACTIVE_INST_A": -97.3, "POWER_ACTIVE_INST_B": -344.9,
    "POWER_REACTIVE_INST_TOTAL": 193.0,
    "FACTOR_POTENCIA_TOTAL": 0.75,
    "POWER_ACTIVE_TOTAL_POS": 3083.27,
    "POWER_ACTIVE_TOTAL_NEG": 1846.6
  },
  "success": true,
  "device_type": "CT_Meter",
  "error": null
}"""


@dataclass
class FakeMessage:
    payload: bytes


def make_service(handler_calls: list[DeviceReading]) -> MQTTService:
    async def handler(reading: DeviceReading) -> None:
        handler_calls.append(reading)

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    return MQTTService(settings, handler)


def test_real_payload_parses() -> None:
    reading = DeviceReading.model_validate_json(REAL_PAYLOAD)
    assert reading.device_id == 11
    assert reading.device_type == "CT_Meter"
    assert reading.data["POWER_ACTIVE_TOTAL_POS"] == 3083.27
    assert reading.timestamp.tzinfo is not None


async def test_on_message_invokes_handler() -> None:
    calls: list[DeviceReading] = []
    service = make_service(calls)
    await service._on_message(FakeMessage(REAL_PAYLOAD))  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    assert len(calls) == 1
    assert calls[0].device_name == "Modbus_DTSU666_11"


async def test_invalid_payload_ignored() -> None:
    calls: list[DeviceReading] = []
    service = make_service(calls)
    await service._on_message(FakeMessage(b'{"garbage": true}'))  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    await service._on_message(FakeMessage(b"not json"))  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    assert calls == []
