"""Cliente MQTT — solo consumidor del tópico del script de adquisición.

Deliberadamente pequeño: un loop con reconexión automática que parsea cada
payload y lo entrega a un handler async. El destino del dato (estado en
memoria, WebSocket) se inyecta como callback en fases posteriores.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import aiomqtt
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.mqtt import DeviceReading

logger = get_logger("apiems.mqtt")

type ReadingHandler = Callable[[DeviceReading], Awaitable[None]]

RECONNECT_SECONDS = 5


class MQTTService:
    def __init__(self, settings: Settings, handler: ReadingHandler) -> None:
        self._settings = settings
        self._handler = handler
        self._task: asyncio.Task[None] | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="mqtt-consumer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            self._connected = False
            logger.info("mqtt_stopped")

    async def _run(self) -> None:
        settings = self._settings
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=settings.MQTT_HOST,
                    port=settings.MQTT_PORT,
                    username=settings.MQTT_USER or None,
                    password=settings.MQTT_PASSWORD or None,
                    identifier=settings.MQTT_CLIENT_ID,
                    clean_session=False,  # el broker retiene mensajes QoS1 si nos caemos
                ) as client:
                    await client.subscribe(settings.MQTT_TOPIC, qos=settings.MQTT_QOS)
                    self._connected = True
                    logger.info(
                        "mqtt_connected",
                        host=settings.MQTT_HOST,
                        topic=settings.MQTT_TOPIC,
                        qos=settings.MQTT_QOS,
                    )
                    async for message in client.messages:
                        await self._on_message(message)
            except aiomqtt.MqttError as exc:
                self._connected = False
                logger.warning(
                    "mqtt_disconnected", error=str(exc), retry_in_seconds=RECONNECT_SECONDS
                )
                await asyncio.sleep(RECONNECT_SECONDS)

    async def _on_message(self, message: aiomqtt.Message) -> None:
        try:
            reading = DeviceReading.model_validate_json(message.payload)
        except ValidationError as exc:
            logger.warning("mqtt_payload_invalid", errors=exc.error_count())
            return
        await self._handler(reading)
