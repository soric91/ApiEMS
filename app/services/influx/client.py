"""Cliente InfluxDB async — instancia única gestionada por el lifespan de la app."""

from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync
from influxdb_client.client.query_api_async import QueryApiAsync

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger("apiems.influx")


class InfluxService:
    """Singleton de conexión a InfluxDB 2.x (una instancia por aplicación)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: InfluxDBClientAsync | None = None

    async def connect(self) -> None:
        self._client = InfluxDBClientAsync(
            url=self._settings.INFLUX_URL,
            token=self._settings.INFLUX_TOKEN,
            org=self._settings.INFLUX_ORG,
            timeout=self._settings.INFLUX_TIMEOUT_MS,
        )
        logger.info(
            "influx_client_ready",
            url=self._settings.INFLUX_URL,
            timeout_ms=self._settings.INFLUX_TIMEOUT_MS,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("influx_client_closed")

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except Exception as exc:
            logger.warning("influx_ping_failed", error=str(exc))
            return False

    @property
    def query_api(self) -> QueryApiAsync:
        if self._client is None:
            raise RuntimeError("InfluxService no conectado; llamar connect() primero")
        return self._client.query_api()
