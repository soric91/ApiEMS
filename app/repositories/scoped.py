"""El repositorio de InfluxDB, ya acotado a los equipos de un cliente.

El recorte por cliente podría aplicarse en cada endpoint, y entonces sería
correcto hasta que alguien agregue un endpoint nuevo y se olvide. Acá se aplica
una sola vez, en el objeto que todos usan: las funciones de servicio siguen
recibiendo un `device_id` opcional y llamando a los mismos métodos, sin saber
que el repositorio que tienen en la mano ya no puede ver otra empresa.

Un `device_id` ajeno responde 404 en vez de 403: confirmar que existe ya sería
contar algo de otro cliente.
"""

from collections.abc import AsyncGenerator, Sequence
from datetime import datetime, timedelta

from fastapi import HTTPException, status

from app.models.variables import Aggregation, Variable
from app.repositories.influx import EnergyRecord, InfluxRepository
from app.schemas.influx import EnergyPoint, TimeSeriesPoint


class ScopedInfluxRepository:
    """Envuelve el repositorio real inyectando la flota en cada consulta."""

    def __init__(self, inner: InfluxRepository, devices: frozenset[str]) -> None:
        self._inner = inner
        self._devices: tuple[str, ...] = tuple(sorted(devices))

    def _check(self, device_id: str | None) -> str | None:
        if device_id is not None and device_id not in self._devices:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Dispositivo {device_id} no encontrado",
            )
        return device_id

    @property
    def _scope(self) -> Sequence[str]:
        return self._devices

    @property
    def cache_identity(self) -> str:
        """Qué empresa está preguntando, para la clave del caché.

        Existe porque `@cached` armaba su clave con `str()` de los argumentos, y
        el primero es este objeto: sin `__str__` propio, eso da su **dirección
        de memoria**. Como se crea uno nuevo por petición, el recolector
        reutiliza direcciones, y el repositorio de otra empresa podía caer en la
        misma — sirviendo el consumo de un cliente a otro.

        La identidad son los equipos que puede ver, que es exactamente lo que
        hace distinta una respuesta de otra. Va como propiedad y no como
        `__str__` para que sea parte explícita del contrato: un `__str__` lo
        esconde, y alguien puede borrarlo sin ver qué rompe.
        """
        return "|".join(self._devices)

    async def instant_series(
        self,
        variable: Variable,
        start: datetime,
        stop: datetime,
        every: timedelta,
        aggregation: Aggregation = Aggregation.MEAN,
        device_id: str | None = None,
    ) -> list[TimeSeriesPoint]:
        return await self._inner.instant_series(
            variable,
            start,
            stop,
            every,
            aggregation,
            self._check(device_id),
            devices=self._scope,
        )

    async def last_value(
        self,
        variable: Variable,
        device_id: str | None = None,
        lookback: timedelta = timedelta(hours=1),
    ) -> TimeSeriesPoint | None:
        return await self._inner.last_value(
            variable, self._check(device_id), devices=self._scope, lookback=lookback
        )

    async def instant_reduce(
        self,
        variable: Variable,
        start: datetime,
        stop: datetime,
        aggregation: Aggregation,
        device_id: str | None = None,
    ) -> float | None:
        return await self._inner.instant_reduce(
            variable,
            start,
            stop,
            aggregation,
            self._check(device_id),
            devices=self._scope,
        )

    async def energy_series(
        self,
        counter: Variable,
        start: datetime,
        stop: datetime,
        every: timedelta,
        device_id: str | None = None,
    ) -> list[EnergyPoint]:
        return await self._inner.energy_series(
            counter, start, stop, every, self._check(device_id), devices=self._scope
        )

    async def energy_total(
        self,
        counter: Variable,
        start: datetime,
        stop: datetime,
        device_id: str | None = None,
    ) -> float:
        return await self._inner.energy_total(
            counter, start, stop, self._check(device_id), devices=self._scope
        )

    async def energy_totals_by_counter(
        self,
        counters: Sequence[Variable],
        start: datetime,
        stop: datetime,
        device_id: str | None = None,
    ) -> dict[Variable, float]:
        return await self._inner.energy_totals_by_counter(
            counters, start, stop, self._check(device_id), devices=self._scope
        )

    async def energy_series_by_counter(
        self,
        counters: Sequence[Variable],
        start: datetime,
        stop: datetime,
        every: timedelta,
        device_id: str | None = None,
    ) -> dict[Variable, list[EnergyPoint]]:
        return await self._inner.energy_series_by_counter(
            counters, start, stop, every, self._check(device_id), devices=self._scope
        )

    async def energy_records(
        self,
        counters: Sequence[Variable],
        start: datetime,
        stop: datetime,
        device_id: str | None = None,
    ) -> AsyncGenerator[EnergyRecord]:
        """Streaming de puntos crudos, ya acotado a la flota.

        El `_check` corre ANTES de devolver el generador: un `device_id` ajeno
        da 404 sin haber empezado a volcar CSV.
        """
        checked = self._check(device_id)
        return await self._inner.energy_records(counters, start, stop, checked, devices=self._scope)

    async def field_keys(self, lookback: timedelta = timedelta(days=30)) -> list[str]:
        """Los campos con datos, ya acotados a los equipos de este cliente."""
        return await self._inner.field_keys(self._scope, lookback)

    async def list_device_ids(self, lookback: timedelta = timedelta(days=30)) -> list[str]:
        """Solo los equipos de este cliente que además reportaron algo.

        La intersección importa: la flota dice qué existe en el CRM, InfluxDB
        dice qué llegó a publicar. Un equipo dado de alta pero nunca conectado
        no debería aparecer como si estuviera midiendo.
        """
        seen = await self._inner.list_device_ids(lookback)
        return [device for device in seen if device in self._devices]
