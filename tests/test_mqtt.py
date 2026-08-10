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
    "PhV_phsA": 120.4, "PhV_phsB": 121.2,
    "A_phsA": 1.93, "A_phsB": 2.81,
    "TotW": -442.2,
    "W_phsA": -97.3, "W_phsB": -344.9,
    "TotVAr": 193.0,
    "TotPF": 0.75,
    "TotWh_import": 3083.27,
    "TotWh_export": 1846.6
  },
  "success": true,
  "device_type": "CT_Meter",
  "error": null
}"""


@dataclass
class FakeMessage:
    payload: bytes
    topic: str = "gatewayems/modbus/11/bf6a469f-4c2a-4402-9438-49a491ad2238"


def make_service(handler_calls: list[DeviceReading]) -> MQTTService:
    async def handler(reading: DeviceReading) -> None:
        handler_calls.append(reading)

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    return MQTTService(settings, handler)


def test_real_payload_parses() -> None:
    reading = DeviceReading.model_validate_json(REAL_PAYLOAD)
    assert reading.device_id == 11
    assert reading.device_type == "CT_Meter"
    assert reading.data["TotWh_import"] == 3083.27
    assert reading.timestamp.tzinfo is not None


async def test_on_message_invokes_handler() -> None:
    calls: list[DeviceReading] = []
    service = make_service(calls)
    await service._on_message(FakeMessage(REAL_PAYLOAD))  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    assert len(calls) == 1
    assert calls[0].device_name == "Modbus_DTSU666_11"


async def test_on_message_extracts_equipment_uuid_from_topic() -> None:
    calls: list[DeviceReading] = []
    service = make_service(calls)
    # UUID del tópico == identify_device del payload: caso normal, sin mismatch.
    message = FakeMessage(
        REAL_PAYLOAD, topic="gatewayems/modbus/11/bf6a469f-4c2a-4402-9438-49a491ad2238"
    )
    await service._on_message(message)  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    assert calls[0].equipment_uuid == "bf6a469f-4c2a-4402-9438-49a491ad2238"
    assert calls[0].modbus_id == 11


async def test_on_message_tolerates_unparseable_topic() -> None:
    """Un tópico con forma inesperada no debe tumbar el consumidor — solo
    equipment_uuid/modbus_id quedan en None, el resto del payload se
    entrega igual."""
    calls: list[DeviceReading] = []
    service = make_service(calls)
    await service._on_message(FakeMessage(REAL_PAYLOAD, topic="algo/inesperado"))  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    assert len(calls) == 1
    assert calls[0].equipment_uuid is None
    assert calls[0].modbus_id is None


async def test_on_message_logs_mismatch_between_topic_and_payload_identity() -> None:
    """El tópico y el payload deberían traer el mismo UUID de equipo — si el
    script de adquisición está mal configurado y difieren, no debe fallar
    (la lectura se entrega igual), pero sí quedar loggeado para detectarlo."""
    calls: list[DeviceReading] = []
    service = make_service(calls)
    message = FakeMessage(
        REAL_PAYLOAD, topic="gatewayems/modbus/74/7d8704bd-5fe0-4686-972e-a71febc718d7"
    )
    await service._on_message(message)  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    assert len(calls) == 1
    assert calls[0].equipment_uuid == "7d8704bd-5fe0-4686-972e-a71febc718d7"
    assert calls[0].identify_device == "bf6a469f-4c2a-4402-9438-49a491ad2238"


async def test_invalid_payload_ignored() -> None:
    calls: list[DeviceReading] = []
    service = make_service(calls)
    await service._on_message(FakeMessage(b'{"garbage": true}'))  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    await service._on_message(FakeMessage(b"not json"))  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    assert calls == []


async def test_non_finite_values_dropped_variable_by_variable() -> None:
    """Un valor NaN o Inf en una variable no puede salir por el WebSocket:
    JSON no los representa (orjson escribe null) y el chart del panel revienta.
    Se filtra la variable puntual sin tirar el resto de la lectura."""
    calls: list[DeviceReading] = []
    service = make_service(calls)
    payload = REAL_PAYLOAD.replace(
        b'"TotW": -442.2',
        b'"TotW": 1e999',  # -> inf, pydantic lo acepta
    )
    await service._on_message(FakeMessage(payload))  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    assert len(calls) == 1
    assert "TotW" not in calls[0].data
    assert calls[0].data["PhV_phsA"] == 120.4
    assert calls[0].data["TotWh_import"] == 3083.27
