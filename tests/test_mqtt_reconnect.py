"""Prueba el loop de reconexión real de MQTTService (start/stop/_run),
sustituyendo aiomqtt.Client por un doble que falla una vez y luego conecta.
"""

import asyncio
from typing import Any

import aiomqtt
import pytest

from app.core.config import Settings
from app.schemas.mqtt import DeviceReading
from app.services.mqtt.client import MQTTService

PAYLOAD = (
    b'{"device_name":"d","device_id":1,"identify_device":"x",'
    b'"timestamp":"2026-01-01T00:00:00+00:00","data":{"VOLTAGE_A":120.0},'
    b'"success":true,"device_type":"CT_Meter","error":null}'
)


class FakeMessage:
    def __init__(
        self,
        payload: bytes,
        topic: str = "gatewayems/modbus/1/bf6a469f-4c2a-4402-9438-49a491ad2238",
    ) -> None:
        self.payload = payload
        self.topic = topic


class _MessagesIterator:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self._messages = messages

    def __aiter__(self) -> "_MessagesIterator":
        return self

    async def __anext__(self) -> FakeMessage:
        if not self._messages:
            await asyncio.sleep(3600)  # agota mensajes: el test cancela la task antes
            raise StopAsyncIteration
        return self._messages.pop(0)


def _make_fake_client(attempts_before_success: int) -> tuple[type, dict[str, int]]:
    state = {"attempt": 0, "subscribed": 0}

    class FakeMqttClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeMqttClient":
            state["attempt"] += 1
            if state["attempt"] <= attempts_before_success:
                raise aiomqtt.MqttError("broker unreachable")
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def subscribe(self, topic: str, qos: int) -> None:
            state["subscribed"] += 1

        @property
        def messages(self) -> _MessagesIterator:
            return _MessagesIterator([FakeMessage(PAYLOAD)])

    return FakeMqttClient, state


def _settings() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


async def test_reconnects_after_failure_then_delivers_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client_cls, state = _make_fake_client(attempts_before_success=1)
    monkeypatch.setattr("app.services.mqtt.client.aiomqtt.Client", fake_client_cls)
    monkeypatch.setattr("app.services.mqtt.client.RECONNECT_SECONDS", 0)

    received: list[DeviceReading] = []

    async def handler(reading: DeviceReading) -> None:
        received.append(reading)

    service = MQTTService(_settings(), handler)
    assert service.connected is False

    await service.start()
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.02)

    assert state["attempt"] == 2  # 1 fallo + 1 éxito
    assert state["subscribed"] == 1
    assert service.connected is True
    assert len(received) == 1
    assert received[0].device_name == "d"

    await service.stop()
    assert service.connected is False


async def test_stop_without_start_is_noop() -> None:
    async def handler(_reading: DeviceReading) -> None:
        return None

    service = MQTTService(_settings(), handler)
    await service.stop()  # no debe lanzar
    assert service.connected is False
