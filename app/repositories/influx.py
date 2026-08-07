"""Repository de lecturas del medidor en InfluxDB.

Todas las consultas Flux están parametrizadas vía `params` del cliente
(binding del lado del cliente, sin concatenación de valores). Los únicos
fragmentos interpolados en el template provienen de enums internos
(`Aggregation`) o de flags booleanos — nunca de strings del usuario.
"""

from datetime import datetime, timedelta
from typing import Any, Protocol

from influxdb_client.client.query_api_async import QueryApiAsync

from app.models.variables import (
    Aggregation,
    InvalidAggregationError,
    Variable,
    is_cumulative,
)
from app.schemas.influx import EnergyPoint, TimeSeriesPoint
from app.utils.period import flux_window_offset

MEASUREMENT = "Modbus_Data"


class InfluxDataSource(Protocol):
    """Contrato de lectura consumido por los servicios (analytics/kpis/reports).

    Permite que los servicios acepten tanto `InfluxRepository` (producción,
    vía DI) como dobles de prueba, sin acoplarse a la clase concreta.
    """

    async def energy_total(
        self, counter: Variable, start: datetime, stop: datetime, device_id: str | None = None
    ) -> float: ...

    async def energy_series(
        self,
        counter: Variable,
        start: datetime,
        stop: datetime,
        every: timedelta,
        device_id: str | None = None,
    ) -> list[EnergyPoint]: ...

    async def instant_series(
        self,
        variable: Variable,
        start: datetime,
        stop: datetime,
        every: timedelta,
        aggregation: Aggregation = Aggregation.MEAN,
        device_id: str | None = None,
    ) -> list[TimeSeriesPoint]: ...

    async def instant_reduce(
        self,
        variable: Variable,
        start: datetime,
        stop: datetime,
        aggregation: Aggregation,
        device_id: str | None = None,
    ) -> float | None: ...


_BASE_FILTER = """
from(bucket: _bucket)
  |> range(start: _start, stop: _stop)
  |> filter(fn: (r) => r._measurement == _measurement)
  |> filter(fn: (r) => r._field == _field)
"""

# El script de adquisición tagea cada punto con `device_id` (entero, único
# solo DENTRO de un gateway/bus) e `identify_device` (UUID, único en toda la
# flota — confirmado en vivo contra InfluxDB). El parámetro público de la API
# se sigue llamando `device_id`, pero internamente filtra por el tag
# `identify_device`: es el único que no colisiona entre gateways.
_DEVICE_FILTER = "  |> filter(fn: (r) => r.identify_device == _device_id)\n"


class InfluxRepository:
    def __init__(self, query_api: QueryApiAsync, bucket: str, tz_name: str = "UTC") -> None:
        self._query_api = query_api
        self._bucket = bucket
        self._tz_name = tz_name

    # ------------------------------------------------------------------
    # Variables instantáneas
    # ------------------------------------------------------------------
    async def instant_series(
        self,
        variable: Variable,
        start: datetime,
        stop: datetime,
        every: timedelta,
        aggregation: Aggregation = Aggregation.MEAN,
        device_id: str | None = None,
    ) -> list[TimeSeriesPoint]:
        """Serie agregada (mean/max/min/last) de una variable instantánea."""
        if is_cumulative(variable):
            raise InvalidAggregationError(variable, aggregation)

        flux = (
            _BASE_FILTER
            + (_DEVICE_FILTER if device_id is not None else "")
            + "  |> aggregateWindow(every: _every, offset: _offset, "
            + f"fn: {aggregation.value}, createEmpty: false)\n"
        )
        params = self._params(
            field=variable,
            start=start,
            stop=stop,
            every=every,
            device_id=device_id,
        )
        params["_offset"] = flux_window_offset(self._tz_name, start)
        tables = await self._query(flux, params)
        return [TimeSeriesPoint(time=t, value=v) for t, v in self._records(tables)]

    async def last_value(
        self,
        variable: Variable,
        device_id: str | None = None,
        lookback: timedelta = timedelta(hours=1),
    ) -> TimeSeriesPoint | None:
        """Último valor registrado de cualquier variable (instantánea o contador)."""
        flux = (
            "from(bucket: _bucket)\n"
            "  |> range(start: _start)\n"
            "  |> filter(fn: (r) => r._measurement == _measurement)\n"
            "  |> filter(fn: (r) => r._field == _field)\n"
            + (_DEVICE_FILTER if device_id is not None else "")
            + "  |> last()\n"
        )
        params = self._params(field=variable, start=-lookback, device_id=device_id)
        tables = await self._query(flux, params)
        records = self._records(tables)
        if not records:
            return None
        time, value = records[-1]
        return TimeSeriesPoint(time=time, value=value)

    async def instant_reduce(
        self,
        variable: Variable,
        start: datetime,
        stop: datetime,
        aggregation: Aggregation,
        device_id: str | None = None,
    ) -> float | None:
        """Reduce todo el rango a un solo valor (mean/max/min/last), sin ventana.

        Para resúmenes de rango (p. ej. /history/range); no usar para series.
        """
        if is_cumulative(variable):
            raise InvalidAggregationError(variable, aggregation)

        flux = (
            _BASE_FILTER
            + (_DEVICE_FILTER if device_id is not None else "")
            + f"  |> {aggregation.value}()\n"
        )
        params = self._params(field=variable, start=start, stop=stop, device_id=device_id)
        tables = await self._query(flux, params)
        values = self._values(tables)
        return values[0] if values else None

    # ------------------------------------------------------------------
    # Contadores acumulativos (energía)
    # ------------------------------------------------------------------
    async def energy_series(
        self,
        counter: Variable,
        start: datetime,
        stop: datetime,
        every: timedelta,
        device_id: str | None = None,
    ) -> list[EnergyPoint]:
        """Energía (kWh) por ventana: difference() sobre el contador acumulativo.

        El rango se extiende una ventana hacia atrás para que difference()
        no descarte la primera ventana solicitada.
        """
        if not is_cumulative(counter):
            raise ValueError(f"'{counter}' no es un contador acumulativo")

        flux = (
            _BASE_FILTER
            + (_DEVICE_FILTER if device_id is not None else "")
            + "  |> aggregateWindow(every: _every, offset: _offset, fn: last, createEmpty: false)\n"
            + "  |> difference(nonNegative: true)\n"
        )
        params = self._params(
            field=counter,
            start=start - every,
            stop=stop,
            every=every,
            device_id=device_id,
        )
        params["_offset"] = flux_window_offset(self._tz_name, start)
        tables = await self._query(flux, params)
        return [EnergyPoint(time=t, value=v) for t, v in self._records(tables) if t > start]

    async def energy_total(
        self,
        counter: Variable,
        start: datetime,
        stop: datetime,
        device_id: str | None = None,
    ) -> float:
        """Energía total (kWh) en el rango: spread() = last - first del contador."""
        if not is_cumulative(counter):
            raise ValueError(f"'{counter}' no es un contador acumulativo")

        flux = _BASE_FILTER + (_DEVICE_FILTER if device_id is not None else "") + "  |> spread()\n"
        params = self._params(field=counter, start=start, stop=stop, device_id=device_id)
        tables = await self._query(flux, params)
        return round(sum(self._values(tables)), 2)

    # ------------------------------------------------------------------
    # Dispositivos
    # ------------------------------------------------------------------
    async def list_device_ids(self, lookback: timedelta = timedelta(days=30)) -> list[str]:
        flux = """
import "influxdata/influxdb/schema"
schema.tagValues(bucket: _bucket, tag: "identify_device", start: _start)
"""
        params: dict[str, Any] = {"_bucket": self._bucket, "_start": -lookback}
        tables = await self._query(flux, params)
        return sorted(
            str(record.get_value())
            for table in tables
            for record in table.records
            if record.get_value() is not None
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _query(self, flux: str, params: dict[str, Any]) -> Any:
        # influxdb-client no expone tipos estrictos en query(); aislar aquí
        return await self._query_api.query(flux, params=params)  # pyright: ignore[reportUnknownMemberType]

    def _params(
        self,
        *,
        field: Variable,
        start: datetime | timedelta,
        stop: datetime | None = None,
        every: timedelta | None = None,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "_bucket": self._bucket,
            "_measurement": MEASUREMENT,
            "_field": field.value,
            "_start": start,
        }
        if stop is not None:
            params["_stop"] = stop
        if every is not None:
            params["_every"] = every
        if device_id is not None:
            params["_device_id"] = device_id
        return params

    @staticmethod
    def _records(tables: Any) -> list[tuple[datetime, float]]:
        out: list[tuple[datetime, float]] = []
        for table in tables:
            for record in table.records:
                time = record.values.get("_time")
                value = record.values.get("_value")
                if time is not None and value is not None:
                    out.append((time, round(float(value), 2)))
        out.sort(key=lambda item: item[0])
        return out

    @staticmethod
    def _values(tables: Any) -> list[float]:
        """Valores de tablas sin columna _time (spread, sum, count...).

        Redondeados a 2 decimales: mean()/spread() sobre floats acumulan
        ruido de punto flotante (p. ej. 10.690000000000055) sin aportar
        precisión real más allá de la que reporta el medidor.
        """
        return [
            round(float(record.values["_value"]), 2)
            for table in tables
            for record in table.records
            if record.values.get("_value") is not None
        ]
