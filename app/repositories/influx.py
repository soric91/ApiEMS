"""Repository de lecturas del medidor en InfluxDB.

Todas las consultas Flux están parametrizadas vía `params` del cliente
(binding del lado del cliente, sin concatenación de valores). Los únicos
fragmentos interpolados en el template provienen de enums internos
(`Aggregation`) o de flags booleanos — nunca de strings del usuario.
"""

from collections.abc import Sequence
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

    Permite que los servicios acepten tanto `InfluxRepository` (crudo, para
    tareas internas), `ScopedInfluxRepository` (lo que ve un endpoint, ya
    acotado a un cliente) como dobles de prueba, sin acoplarse a ninguno.

    A propósito NO menciona `devices`: el recorte por flota es asunto del
    envoltorio, y un servicio que pudiera pasarlo podría también omitirlo.
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

    async def energy_totals_by_counter(
        self,
        counters: Sequence[Variable],
        start: datetime,
        stop: datetime,
        device_id: str | None = None,
    ) -> dict[Variable, float]: ...

    async def energy_series_by_counter(
        self,
        counters: Sequence[Variable],
        start: datetime,
        stop: datetime,
        every: timedelta,
        device_id: str | None = None,
    ) -> dict[Variable, list[EnergyPoint]]: ...


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
_ONE_DEVICE_FILTER = "  |> filter(fn: (r) => r.identify_device == _device_id)\n"
# Recorte por cliente. Sin esto, una consulta sin `device_id` agregaría los
# equipos de TODAS las empresas — que es exactamente la fuga que el filtro por
# flota existe para impedir.
_MANY_DEVICES_FILTER = "  |> filter(fn: (r) => contains(value: r.identify_device, set: _devices))\n"


def _device_filter(device_id: str | None, devices: Sequence[str] | None) -> str:
    """El fragmento Flux que acota la consulta a lo que el que llama puede ver.

    Un `device_id` concreto gana: ya viene validado contra la flota, así que
    volver a aplicar el conjunto sería redundante. Sin él, se acota al
    conjunto. Sin ninguno de los dos no hay recorte, que solo es correcto para
    un llamador sin dueño (una tarea interna), nunca para una petición HTTP.
    """
    if device_id is not None:
        return _ONE_DEVICE_FILTER
    if devices is not None:
        return _MANY_DEVICES_FILTER
    return ""


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
        devices: Sequence[str] | None = None,
    ) -> list[TimeSeriesPoint]:
        """Serie agregada (mean/max/min/last) de una variable instantánea."""
        if is_cumulative(variable):
            raise InvalidAggregationError(variable, aggregation)

        flux = (
            _BASE_FILTER
            + _device_filter(device_id, devices)
            + "  |> aggregateWindow(every: _every, offset: _offset, "
            + f"fn: {aggregation.value}, createEmpty: false)\n"
        )
        params = self._params(
            field=variable,
            start=start,
            stop=stop,
            every=every,
            device_id=device_id,
            devices=devices,
        )
        params["_offset"] = flux_window_offset(self._tz_name, start)
        tables = await self._query(flux, params)
        return [TimeSeriesPoint(time=t, value=v) for t, v in self._records(tables)]

    async def last_value(
        self,
        variable: Variable,
        device_id: str | None = None,
        devices: Sequence[str] | None = None,
        lookback: timedelta = timedelta(hours=1),
    ) -> TimeSeriesPoint | None:
        """Último valor registrado de cualquier variable (instantánea o contador)."""
        flux = (
            "from(bucket: _bucket)\n"
            "  |> range(start: _start)\n"
            "  |> filter(fn: (r) => r._measurement == _measurement)\n"
            "  |> filter(fn: (r) => r._field == _field)\n"
            + _device_filter(device_id, devices)
            + "  |> last()\n"
        )
        params = self._params(field=variable, start=-lookback, device_id=device_id, devices=devices)
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
        devices: Sequence[str] | None = None,
    ) -> float | None:
        """Reduce todo el rango a un solo valor (mean/max/min/last), sin ventana.

        Para resúmenes de rango (p. ej. /history/range); no usar para series.
        """
        if is_cumulative(variable):
            raise InvalidAggregationError(variable, aggregation)

        flux = _BASE_FILTER + _device_filter(device_id, devices) + f"  |> {aggregation.value}()\n"
        params = self._params(
            field=variable, start=start, stop=stop, device_id=device_id, devices=devices
        )
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
        devices: Sequence[str] | None = None,
    ) -> list[EnergyPoint]:
        """Energía (kWh) por ventana: difference() sobre el contador acumulativo.

        El rango se extiende una ventana hacia atrás para que difference()
        no descarte la primera ventana solicitada.
        """
        if not is_cumulative(counter):
            raise ValueError(f"'{counter}' no es un contador acumulativo")

        flux = (
            _BASE_FILTER
            + _device_filter(device_id, devices)
            + "  |> aggregateWindow(every: _every, offset: _offset, fn: last, createEmpty: false)\n"
            + "  |> difference(nonNegative: true)\n"
        )
        params = self._params(
            field=counter,
            start=start - every,
            stop=stop,
            every=every,
            device_id=device_id,
            devices=devices,
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
        devices: Sequence[str] | None = None,
    ) -> float:
        """Energía total (kWh) en el rango: spread() = last - first del contador."""
        if not is_cumulative(counter):
            raise ValueError(f"'{counter}' no es un contador acumulativo")

        flux = _BASE_FILTER + _device_filter(device_id, devices) + "  |> spread()\n"
        params = self._params(
            field=counter, start=start, stop=stop, device_id=device_id, devices=devices
        )
        tables = await self._query(flux, params)
        return round(sum(self._values(tables)), 2)

    async def energy_totals_by_counter(
        self,
        counters: Sequence[Variable],
        start: datetime,
        stop: datetime,
        device_id: str | None = None,
        devices: Sequence[str] | None = None,
    ) -> dict[Variable, float]:
        """Energía de VARIOS contadores en una sola consulta.

        Un `filter` por conjunto de campos y un `group` por `_field` (uno por
        cuadrante); `spread()` reduce cada grupo a `last - first`. Antes esto
        eran N consultas —contadores activos y los cuatro cuadrantes reactivos
        suelen pedirse juntos— y cada una pagaba la misma lectura del rango.
        """
        for c in counters:
            if not is_cumulative(c):
                raise ValueError(f"'{c}' no es un contador acumulativo")

        flux = (
            "from(bucket: _bucket)\n"
            "  |> range(start: _start, stop: _stop)\n"
            "  |> filter(fn: (r) => r._measurement == _measurement)\n"
            "  |> filter(fn: (r) => contains(value: r._field, set: _fields))\n"
            + _device_filter(device_id, devices)
            + '  |> group(columns: ["_field"])\n'
            + "  |> spread()\n"
        )
        params: dict[str, Any] = {
            "_bucket": self._bucket,
            "_measurement": MEASUREMENT,
            "_fields": [c.value for c in counters],
            "_start": start,
            "_stop": stop,
        }
        if device_id is not None:
            params["_device_id"] = device_id
        elif devices is not None:
            params["_devices"] = list(devices)

        tables = await self._query(flux, params)
        result: dict[Variable, float] = dict.fromkeys(counters, 0.0)
        for table in tables:
            if not table.records:
                continue
            field = table.records[0].values.get("_field")
            values = self._values([table])
            if field is not None and values:
                result[Variable(field)] = values[0]
        return result

    async def energy_series_by_counter(
        self,
        counters: Sequence[Variable],
        start: datetime,
        stop: datetime,
        every: timedelta,
        device_id: str | None = None,
        devices: Sequence[str] | None = None,
    ) -> dict[Variable, list[EnergyPoint]]:
        """Serie por ventana de VARIOS contadores en una sola consulta.

        Mismo `group` por `_field` que `energy_totals_by_counter`, con
        `difference()` por ventana a continuación. El rango se extiende una
        ventana hacia atrás para que la primera ventana solicitada no pierda
        su diferencia (igual que en `energy_series`).
        """
        for c in counters:
            if not is_cumulative(c):
                raise ValueError(f"'{c}' no es un contador acumulativo")

        flux = (
            "from(bucket: _bucket)\n"
            "  |> range(start: _start, stop: _stop)\n"
            "  |> filter(fn: (r) => r._measurement == _measurement)\n"
            "  |> filter(fn: (r) => contains(value: r._field, set: _fields))\n"
            + _device_filter(device_id, devices)
            + '  |> group(columns: ["_field"])\n'
            + "  |> aggregateWindow(every: _every, offset: _offset, fn: last, createEmpty: false)\n"
            + '  |> difference(nonNegative: true, columns: ["_value"])\n'
        )
        params: dict[str, Any] = {
            "_bucket": self._bucket,
            "_measurement": MEASUREMENT,
            "_fields": [c.value for c in counters],
            "_start": start - every,
            "_stop": stop,
            "_every": every,
            "_offset": flux_window_offset(self._tz_name, start),
        }
        if device_id is not None:
            params["_device_id"] = device_id
        elif devices is not None:
            params["_devices"] = list(devices)

        tables = await self._query(flux, params)
        result: dict[Variable, list[EnergyPoint]] = {c: [] for c in counters}
        for table in tables:
            if not table.records:
                continue
            field = table.records[0].values.get("_field")
            if field is None:
                continue
            points = [EnergyPoint(time=t, value=v) for t, v in self._records([table]) if t > start]
            result[Variable(field)] = points
        return result

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

    async def field_keys(
        self,
        devices: Sequence[str],
        lookback: timedelta = timedelta(days=30),
    ) -> list[str]:
        """Qué campos reportaron algo estos equipos, en la ventana dada.

        Es la mitad de la respuesta a "qué se puede graficar": la otra mitad
        —qué significa cada nombre— la tiene el CRM. Sin esto, un panel
        dibujaría una gráfica de fase C para un medidor monofásico y la
        mostraría vacía para siempre.

        `schema.fieldKeys` con predicado deja que Influx resuelva contra sus
        metadatos en vez de leer las series enteras; escanear 30 días de
        puntos solo para saber qué nombres existen sería caro y se paga en
        cada carga del panel.
        """
        if not devices:
            return []

        flux = """
import "influxdata/influxdb/schema"
schema.fieldKeys(
    bucket: _bucket,
    predicate: (r) =>
        r._measurement == _measurement and
        contains(value: r.identify_device, set: _devices),
    start: _start,
)
"""
        params: dict[str, Any] = {
            "_bucket": self._bucket,
            "_measurement": MEASUREMENT,
            "_devices": list(devices),
            "_start": -lookback,
        }
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
        devices: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "_bucket": self._bucket,
            "_measurement": MEASUREMENT,
            # El nombre de la variable ES el campo: no hay traducción de por medio.
            "_field": field.value,
            "_start": start,
        }
        if stop is not None:
            params["_stop"] = stop
        if every is not None:
            params["_every"] = every
        if device_id is not None:
            params["_device_id"] = device_id
        elif devices is not None:
            # Lista, no set: el cliente de Influx serializa a JSON y `set` no
            # es serializable. `contains` acepta el array igual.
            params["_devices"] = list(devices)
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
