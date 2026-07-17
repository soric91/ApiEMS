"""Dobles de prueba compartidos entre archivos de test."""

from datetime import datetime, timedelta
from typing import Any

from app.models.variables import Aggregation, Variable
from app.schemas.influx import EnergyPoint, TimeSeriesPoint


class FakeInfluxRepository:
    """Repositorio Influx falso: valores configurables, sin red.

    Implementa el mismo contrato público que InfluxRepository (duck typing);
    no hereda de ella para no arrastrar el cliente real.
    """

    def __init__(self) -> None:
        self.energy_total_value: float = 5.5
        self.energy_series_points: list[EnergyPoint] = []
        self.instant_series_points: list[TimeSeriesPoint] = []
        self.instant_reduce_value: float | None = 100.0
        self.calls: list[tuple[str, ...]] = []
        # Overrides opcionales por variable/contador, para tests que
        # necesitan distinguir p. ej. importación vs exportación.
        self.energy_series_by_counter: dict[Variable, list[EnergyPoint]] = {}
        self.instant_series_by_variable: dict[Variable, list[TimeSeriesPoint]] = {}
        self.energy_total_by_counter: dict[Variable, float] = {}

    async def energy_total(
        self,
        counter: Variable,
        start: datetime,
        stop: datetime,
        device_id: str | None = None,
    ) -> float:
        self.calls.append(("energy_total", counter.value, str(device_id)))
        return self.energy_total_by_counter.get(counter, self.energy_total_value)

    async def energy_series(
        self,
        counter: Variable,
        start: datetime,
        stop: datetime,
        every: timedelta,
        device_id: str | None = None,
    ) -> list[EnergyPoint]:
        self.calls.append(("energy_series", counter.value, str(device_id)))
        return self.energy_series_by_counter.get(counter, self.energy_series_points)

    async def instant_series(
        self,
        variable: Variable,
        start: datetime,
        stop: datetime,
        every: timedelta,
        aggregation: Aggregation = Aggregation.MEAN,
        device_id: str | None = None,
    ) -> list[TimeSeriesPoint]:
        self.calls.append(("instant_series", variable.value, aggregation.value, str(device_id)))
        return self.instant_series_by_variable.get(variable, self.instant_series_points)

    async def instant_reduce(
        self,
        variable: Variable,
        start: datetime,
        stop: datetime,
        aggregation: Aggregation,
        device_id: str | None = None,
    ) -> float | None:
        self.calls.append(("instant_reduce", variable.value, aggregation.value, str(device_id)))
        return self.instant_reduce_value

    async def list_device_ids(self, lookback: Any = None) -> list[str]:
        return ["11"]


class FakeInfluxService:
    """Sustituye a InfluxService en app.state.influx — sin red."""

    def __init__(self, ping_result: bool = True) -> None:
        self.ping_result = ping_result

    async def ping(self) -> bool:
        return self.ping_result
