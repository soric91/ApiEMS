"""El repositorio de InfluxDB, ya acotado a los equipos de un cliente.

El recorte por cliente podría aplicarse en cada endpoint, y entonces sería
correcto hasta que alguien agregue un endpoint nuevo y se olvide. Acá se aplica
una sola vez, en el objeto que todos usan: las funciones de servicio siguen
recibiendo un `device_id` opcional y llamando a los mismos métodos, sin saber
que el repositorio que tienen en la mano ya no puede ver otra empresa.

Un `device_id` ajeno responde 404 en vez de 403: confirmar que existe ya sería
contar algo de otro cliente.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from fastapi import HTTPException, status

from app.models.variables import Aggregation, Variable
from app.repositories.influx import InfluxRepository
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

    async def field_keys(self, lookback: timedelta = timedelta(days=30)) -> list[str]:
        """Los campos con datos, ya acotados a los equipos de este cliente."""
        return await self._inner.field_keys(self._scope, lookback)

    async def list_device_ids(
        self, lookback: timedelta = timedelta(days=30)
    ) -> list[str]:
        """Solo los equipos de este cliente que además reportaron algo.

        La intersección importa: la flota dice qué existe en el CRM, InfluxDB
        dice qué llegó a publicar. Un equipo dado de alta pero nunca conectado
        no debería aparecer como si estuviera midiendo.
        """
        seen = await self._inner.list_device_ids(lookback)
        return [device for device in seen if device in self._devices]
