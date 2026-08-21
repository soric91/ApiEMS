"""Dobles de prueba compartidos entre archivos de test."""

from collections.abc import Sequence
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
        # Con qué ventana se pidió cada serie de energía: es lo que decide en
        # cuántas barras se reparte el reporte.
        self.energy_series_every: list[timedelta] = []
        self.instant_series_points: list[TimeSeriesPoint] = []
        # Muestras por ventana, para la cobertura de datos.
        self.sample_counts_points: list[TimeSeriesPoint] = []
        self.instant_reduce_value: float | None = 100.0
        # Override por agregación: para distinguir min/max/mean/last en las
        # reducciones escalares (/history/stats).
        self.instant_reduce_by_aggregation: dict[Aggregation, float | None] = {}
        self.calls: list[tuple[object, ...]] = []
        # Overrides opcionales por variable/contador, para tests que
        # necesitan distinguir p. ej. importación vs exportación.
        self.energy_series_points_by_counter: dict[Variable, list[EnergyPoint]] = {}
        self.instant_series_by_variable: dict[Variable, list[TimeSeriesPoint]] = {}
        # Override por (variable, agregación): para distinguir la serie
        # promediada de la de máximos, que es de donde sale la demanda pico.
        self.instant_series_by_aggregation: dict[
            tuple[Variable, Aggregation], list[TimeSeriesPoint]
        ] = {}
        self.energy_total_by_counter: dict[Variable, float] = {}
        # Puntos crudos por contador para el volcado CSV (energy_records).
        self.energy_records_points_by_counter: dict[Variable, list[EnergyPoint]] = {}
        self.energy_records_device_id: str = "bf6a469f-4c2a-4402-9438-49a491ad2238"
        # Qué campos "existen" en Influx, y con qué equipos se preguntó.
        self.field_keys_result: list[str] = []
        self.field_keys_lookback: timedelta | None = None

    async def field_keys(self, lookback: timedelta = timedelta(days=30)) -> list[str]:
        # Firma del repositorio **acotado**, que es a quien sustituye este
        # doble: la flota ya está adentro y no se pasa por parámetro.
        self.field_keys_lookback = lookback
        return sorted(self.field_keys_result)

    async def energy_total(
        self,
        counter: Variable,
        start: datetime,
        stop: datetime,
        device_id: str | None = None,
        devices: Sequence[str] | None = None,
    ) -> float:
        self.calls.append(("energy_total", counter.value, str(device_id), tuple(devices or ())))
        return self.energy_total_by_counter.get(counter, self.energy_total_value)

    async def energy_series(
        self,
        counter: Variable,
        start: datetime,
        stop: datetime,
        every: timedelta,
        device_id: str | None = None,
        devices: Sequence[str] | None = None,
    ) -> list[EnergyPoint]:
        self.calls.append(("energy_series", counter.value, str(device_id), tuple(devices or ())))
        self.energy_series_every.append(every)
        return self.energy_series_points_by_counter.get(counter, self.energy_series_points)

    async def energy_totals_by_counter(
        self,
        counters: Sequence[Variable],
        start: datetime,
        stop: datetime,
        device_id: str | None = None,
        devices: Sequence[str] | None = None,
    ) -> dict[Variable, float]:
        self.calls.append(
            (
                "energy_totals_by_counter",
                tuple(c.value for c in counters),
                str(device_id),
                tuple(devices or ()),
            )
        )
        return {c: self.energy_total_by_counter.get(c, self.energy_total_value) for c in counters}

    async def energy_series_by_counter(
        self,
        counters: Sequence[Variable],
        start: datetime,
        stop: datetime,
        every: timedelta,
        device_id: str | None = None,
        devices: Sequence[str] | None = None,
    ) -> dict[Variable, list[EnergyPoint]]:
        self.calls.append(
            (
                "energy_series_by_counter",
                tuple(c.value for c in counters),
                str(device_id),
                tuple(devices or ()),
            )
        )
        return {
            c: self.energy_series_points_by_counter.get(c, self.energy_series_points)
            for c in counters
        }

    async def energy_records(
        self,
        counters: Sequence[Variable],
        start: datetime,
        stop: datetime,
        device_id: str | None = None,
        devices: Sequence[str] | None = None,
    ) -> Any:
        self.calls.append(
            (
                "energy_records",
                tuple(c.value for c in counters),
                str(device_id),
                tuple(devices or ()),
            )
        )
        return self._iter_energy_records(counters)

    def _iter_energy_records(self, counters: Sequence[Variable]) -> Any:
        async def _iter() -> Any:
            for counter in counters:
                for point in self.energy_records_points_by_counter.get(counter, []):
                    yield (point.time, self.energy_records_device_id, counter.value, point.value)

        return _iter()

    async def instant_series(
        self,
        variable: Variable,
        start: datetime,
        stop: datetime,
        every: timedelta,
        aggregation: Aggregation = Aggregation.MEAN,
        device_id: str | None = None,
        devices: Sequence[str] | None = None,
    ) -> list[TimeSeriesPoint]:
        self.calls.append(
            (
                "instant_series",
                variable.value,
                aggregation.value,
                str(device_id),
                tuple(devices or ()),
            )
        )
        by_aggregation = self.instant_series_by_aggregation.get((variable, aggregation))
        if by_aggregation is not None:
            return by_aggregation
        return self.instant_series_by_variable.get(variable, self.instant_series_points)

    async def sample_counts(
        self,
        variable: Variable,
        start: datetime,
        stop: datetime,
        every: timedelta,
        device_id: str | None = None,
        devices: Sequence[str] | None = None,
    ) -> list[TimeSeriesPoint]:
        self.calls.append(("sample_counts", variable.value, str(device_id), tuple(devices or ())))
        return self.sample_counts_points

    async def instant_reduce(
        self,
        variable: Variable,
        start: datetime,
        stop: datetime,
        aggregation: Aggregation,
        device_id: str | None = None,
        devices: Sequence[str] | None = None,
    ) -> float | None:
        self.calls.append(
            (
                "instant_reduce",
                variable.value,
                aggregation.value,
                str(device_id),
                tuple(devices or ()),
            )
        )
        return self.instant_reduce_by_aggregation.get(aggregation, self.instant_reduce_value)

    async def list_device_ids(self, lookback: Any = None) -> list[str]:
        return ["11"]


class FakeInfluxService:
    """Sustituye a InfluxService en app.state.influx — sin red."""

    def __init__(self, ping_result: bool = True) -> None:
        self.ping_result = ping_result

    async def ping(self) -> bool:
        return self.ping_result
