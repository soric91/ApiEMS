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
    b'"timestamp":"2026-01-01T00:00:00+00:00","data":{"PhV_phsA":120.0},'
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
    def __init__(
        self, messages: list[FakeMessage], al_agotarse: Exception | None = None
    ) -> None:
        self._messages = messages
        self._al_agotarse = al_agotarse

    def __aiter__(self) -> "_MessagesIterator":
        return self

    async def __anext__(self) -> FakeMessage:
        if not self._messages:
            if self._al_agotarse is not None:
                raise self._al_agotarse
            await asyncio.sleep(3600)  # agota mensajes: el test cancela la task antes
            raise StopAsyncIteration
        return self._messages.pop(0)


def _make_fake_client(
    attempts_before_success: int,
    mensajes: int = 1,
    stream_falla: Exception | None = None,
) -> tuple[type, dict[str, int]]:
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
            return _MessagesIterator(
                [FakeMessage(PAYLOAD) for _ in range(mensajes)], stream_falla
            )

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


async def test_un_handler_que_falla_no_se_lleva_el_consumidor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lo que rompió el relay en producción: InfluxDB caído mataba MQTT.

    El handler consulta InfluxDB para evaluar alertas. Cuando esa consulta
    reventaba, la excepción salía del `async for`, el `except MqttError` no la
    agarraba —no es un error de MQTT— y la task moría. No había desconexión ni
    reintento: simplemente no volvía a llegar un mensaje nunca más, y
    `connected` seguía diciendo `True`.

    Se pierde la lectura que falló. Solo esa.
    """
    fake_client_cls, _ = _make_fake_client(attempts_before_success=0, mensajes=3)
    monkeypatch.setattr("app.services.mqtt.client.aiomqtt.Client", fake_client_cls)
    monkeypatch.setattr("app.services.mqtt.client.RECONNECT_SECONDS", 0)

    intentos: list[DeviceReading] = []
    entregados: list[DeviceReading] = []

    async def handler(reading: DeviceReading) -> None:
        intentos.append(reading)
        if len(intentos) == 1:
            raise RuntimeError("influxdb no responde")
        entregados.append(reading)

    service = MQTTService(_settings(), handler)
    await service.start()
    for _ in range(50):
        if len(entregados) == 2:
            break
        await asyncio.sleep(0.02)

    assert len(intentos) == 3  # el primero falló y los otros dos siguieron
    assert len(entregados) == 2
    assert service.connected is True

    await service.stop()


async def test_si_el_consumidor_muere_connected_deja_de_mentir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`create_task` se traga la excepción: sin el callback nadie se entera.

    La falla llega DESPUÉS de conectar y suscribir —la levanta el propio stream
    de mensajes, no el handler, así que el `except` por mensaje no la ve— con
    `_connected` ya en `True`. Que la task termine es aceptable; que
    `/health` siga informando MQTT conectado sin que entre un dato, no.
    """
    fake_client_cls, _ = _make_fake_client(
        attempts_before_success=0, stream_falla=ValueError("stream roto")
    )
    monkeypatch.setattr("app.services.mqtt.client.aiomqtt.Client", fake_client_cls)
    monkeypatch.setattr("app.services.mqtt.client.RECONNECT_SECONDS", 0)

    # Se mira desde adentro del handler y no después: la task muere apenas se
    # agotan los mensajes, así que para cuando el test despierte ya podría
    # estar en False y el chequeo no distinguiría nada.
    conectado_mientras_corria: list[bool] = []

    async def handler(_reading: DeviceReading) -> None:
        conectado_mientras_corria.append(service.connected)

    service = MQTTService(_settings(), handler)
    await service.start()
    for _ in range(50):
        if conectado_mientras_corria:
            break
        await asyncio.sleep(0.02)
    # Sin esto el test pasaría vacío: `connected` arranca en False, así que
    # afirmarlo sin haber conectado antes no prueba nada.
    assert conectado_mientras_corria == [True]

    for _ in range(50):
        if not service.connected:
            break
        await asyncio.sleep(0.02)

    assert service.connected is False


async def test_stop_without_start_is_noop() -> None:
    async def handler(_reading: DeviceReading) -> None:
        return None

    service = MQTTService(_settings(), handler)
    await service.stop()  # no debe lanzar
    assert service.connected is False
