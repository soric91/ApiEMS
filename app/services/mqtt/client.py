"""Cliente MQTT — solo consumidor del tópico del script de adquisición.

Deliberadamente pequeño: un loop con reconexión automática que parsea cada
payload y lo entrega a un handler async. El destino del dato (estado en
memoria, WebSocket) se inyecta como callback en fases posteriores.
"""

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable

import aiomqtt
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.mqtt import DeviceReading

logger = get_logger("apiems.mqtt")

type ReadingHandler = Callable[[DeviceReading], Awaitable[None]]

RECONNECT_SECONDS = 5

# Tópico: "{MQTT_TOPIC}/{modbus_id}/{equipment_uuid}" — un nivel por gateway,
# necesario para no perder mensajes de más de un gateway (el tópico fijo de
# antes solo hacía match exacto, ninguno de estos sub-tópicos calzaba).
# El UUID es del EQUIPO (confirmado: config real del script de adquisición
# tiene `identify_device = <uuid>` por sección de equipo, no por gateway) —
# el mismo valor que ya trae `identify_device` en el payload.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_TOPIC_SUFFIX_SEGMENTS = 2  # {modbus_id}/{equipment_uuid}


def _parse_topic(topic: str, base_topic: str) -> tuple[int | None, str | None]:
    """(modbus_id, equipment_uuid) desde el tópico, o (None, None) si no calza.

    Tolerante a propósito: un tópico con forma inesperada no debe tumbar el
    consumidor, solo dejar esos dos campos vacíos (ver DeviceReading).
    """
    prefix = base_topic.rstrip("/") + "/"
    if not topic.startswith(prefix):
        return None, None
    parts = topic[len(prefix) :].split("/")
    if len(parts) != _TOPIC_SUFFIX_SEGMENTS:
        return None, None
    modbus_id_str, equipment_uuid = parts
    if not _UUID_RE.match(equipment_uuid):
        return None, None
    try:
        return int(modbus_id_str), equipment_uuid
    except ValueError:
        return None, None


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
        self._task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        """El consumidor no debería terminar nunca; si termina, que se sepa.

        `create_task` guarda la excepción dentro del Task y no la levanta en
        ningún lado: sin esto el consumidor muere en silencio y `connected`
        sigue diciendo `True`, así que `/health` informa que MQTT anda mientras
        hace rato que no entra un solo mensaje.
        """
        self._connected = False
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("mqtt_consumer_muerto", error=str(exc), tipo=type(exc).__name__)

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
                    # Sin esto la conexión va en claro aunque el puerto sea el
                    # 8883, y el broker la corta: TLS no se negocia sobre MQTT,
                    # se establece antes. TLSParameters() por defecto usa los
                    # certificados raíz del sistema y verifica el hostname.
                    tls_params=(
                        aiomqtt.TLSParameters() if settings.MQTT_USE_TLS else None
                    ),
                ) as client:
                    wildcard_topic = f"{settings.MQTT_TOPIC.rstrip('/')}/+/+"
                    await client.subscribe(wildcard_topic, qos=settings.MQTT_QOS)
                    self._connected = True
                    logger.info(
                        "mqtt_connected",
                        host=settings.MQTT_HOST,
                        port=settings.MQTT_PORT,
                        tls=settings.MQTT_USE_TLS,
                        topic=wildcard_topic,
                        qos=settings.MQTT_QOS,
                    )
                    async for message in client.messages:
                        # Un mensaje que falla no puede llevarse el loop. Sin
                        # este `except`, cualquier error del handler —InfluxDB
                        # caído al evaluar alertas, por ejemplo— sale del `async
                        # for`, no lo agarra el `except MqttError` de abajo, y
                        # el consumidor muere: se corta el relay para SIEMPRE,
                        # sin desconexión ni reintento, después de UN mensaje
                        # malo. Se pierde esa lectura, no las siguientes.
                        try:
                            await self._on_message(message)
                        except Exception as exc:
                            logger.exception(
                                "mqtt_mensaje_fallido",
                                topic=str(message.topic),
                                error=str(exc),
                            )
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

        modbus_id, equipment_uuid = _parse_topic(str(message.topic), self._settings.MQTT_TOPIC)
        if equipment_uuid is None:
            logger.warning("mqtt_topic_unparseable", topic=str(message.topic))
        else:
            reading.equipment_uuid = equipment_uuid
            reading.modbus_id = modbus_id
            if equipment_uuid != reading.identify_device:
                # El script debería taguear ambos con el mismo UUID de
                # equipo — si difieren, hay una config desalineada en algún
                # lado y conviene enterarse antes de que rompa el selector
                # de medidores, no en silencio.
                logger.warning(
                    "mqtt_topic_identity_mismatch",
                    topic_equipment_uuid=equipment_uuid,
                    payload_identify_device=reading.identify_device,
                )

        await self._handler(reading)
