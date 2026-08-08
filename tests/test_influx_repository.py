from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.models.variables import Aggregation, InvalidAggregationError, Variable
from app.repositories.influx import InfluxRepository
from app.utils.period import flux_window_offset

START = datetime(2026, 7, 1, tzinfo=UTC)
STOP = datetime(2026, 7, 2, tzinfo=UTC)
HOUR = timedelta(hours=1)


class FakeRecord:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def get_value(self) -> Any:
        return self.values.get("_value")


class FakeTable:
    def __init__(self, records: list[FakeRecord]) -> None:
        self.records = records


class FakeQueryApi:
    """Captura la consulta Flux y sus params; devuelve las tablas configuradas."""

    def __init__(self) -> None:
        self.flux: str | None = None
        self.params: dict[str, Any] | None = None
        self.tables: list[FakeTable] = []

    async def query(self, flux: str, params: dict[str, Any]) -> list[Any]:
        self.flux = flux
        self.params = params
        return self.tables


@pytest.fixture
def fake_api() -> FakeQueryApi:
    return FakeQueryApi()


@pytest.fixture
def repo(fake_api: FakeQueryApi) -> InfluxRepository:
    return InfluxRepository(fake_api, "modbus_data_v2")  # pyright: ignore[reportArgumentType]


async def test_instant_series_parameterized(repo: InfluxRepository, fake_api: FakeQueryApi) -> None:
    await repo.instant_series(
        Variable.VOLTAGE_A, START, STOP, HOUR, Aggregation.MAX, device_id="11"
    )
    assert fake_api.flux is not None
    assert fake_api.params == {
        "_bucket": "modbus_data_v2",
        "_measurement": "Modbus_Data",
        # El nombre real del campo, no el público: la consulta se traduce en
        # INFLUX_FIELD porque el medidor guarda `Voltaje_A`.
        "_field": "PhV_phsA",
        "_start": START,
        "_stop": STOP,
        "_every": HOUR,
        "_device_id": "11",
        "_offset": timedelta(0),  # repo de test usa tz_name="UTC" -> offset nulo
    }
    # Valores nunca interpolados en el Flux; solo referencias a params
    assert "11" not in fake_api.flux
    assert "PhV_phsA" not in fake_api.flux
    assert "fn: max" in fake_api.flux
    assert "r.identify_device == _device_id" in fake_api.flux


async def test_instant_series_without_device_filter(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    await repo.instant_series(Variable.CURRENT_A, START, STOP, HOUR)
    assert fake_api.flux is not None
    assert "_device_id" not in fake_api.flux
    assert "fn: mean" in fake_api.flux


async def test_mean_on_counter_rejected(repo: InfluxRepository) -> None:
    with pytest.raises(InvalidAggregationError):
        await repo.instant_series(Variable.POWER_ACTIVE_TOTAL_POS, START, STOP, HOUR)


async def test_energy_series_uses_difference(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    await repo.energy_series(Variable.POWER_ACTIVE_TOTAL_NEG, START, STOP, HOUR)
    assert fake_api.flux is not None
    assert "difference(nonNegative: true)" in fake_api.flux
    assert "fn: last" in fake_api.flux
    assert "mean" not in fake_api.flux
    # Rango extendido una ventana hacia atrás para no perder la primera
    assert fake_api.params is not None
    assert fake_api.params["_start"] == START - HOUR


async def test_energy_series_rejects_instant_variable(repo: InfluxRepository) -> None:
    with pytest.raises(ValueError, match="no es un contador"):
        await repo.energy_series(Variable.VOLTAGE_A, START, STOP, HOUR)


async def test_energy_total_rejects_instant_variable(repo: InfluxRepository) -> None:
    with pytest.raises(ValueError, match="no es un contador"):
        await repo.energy_total(Variable.VOLTAGE_A, START, STOP)


async def test_energy_total_uses_spread(repo: InfluxRepository, fake_api: FakeQueryApi) -> None:
    total = await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, START, STOP)
    assert total == 0.0
    assert fake_api.flux is not None
    assert "spread()" in fake_api.flux
    assert "mean" not in fake_api.flux


async def test_instant_reduce_uses_terminal_aggregation(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    result = await repo.instant_reduce(Variable.VOLTAGE_A, START, STOP, Aggregation.MAX, "11")
    assert result is None  # FakeQueryApi devuelve tablas vacías
    assert fake_api.flux is not None
    assert "|> max()" in fake_api.flux
    assert "aggregateWindow" not in fake_api.flux
    assert fake_api.params is not None
    assert "_every" not in fake_api.params


async def test_instant_reduce_rejects_counter(repo: InfluxRepository) -> None:
    with pytest.raises(InvalidAggregationError):
        await repo.instant_reduce(Variable.POWER_ACTIVE_TOTAL_POS, START, STOP, Aggregation.MEAN)


async def test_values_rounded_to_two_decimals(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """Regresión: mean()/spread() de InfluxDB devuelven ruido de punto flotante
    (10.690000000000055) que no aporta precisión real sobre lo que mide el
    contador (2 decimales)."""
    fake_api.tables = [FakeTable([FakeRecord({"_value": 10.690000000000055})])]
    result = await repo.instant_reduce(Variable.VOLTAGE_A, START, STOP, Aggregation.MEAN)
    assert result == 10.69


async def test_energy_total_sum_rounded(repo: InfluxRepository, fake_api: FakeQueryApi) -> None:
    fake_api.tables = [
        FakeTable(
            [FakeRecord({"_value": 0.1}), FakeRecord({"_value": 0.2})]
        )  # 0.1 + 0.2 == 0.30000000000000004 en float puro
    ]
    total = await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, START, STOP)
    assert total == 0.3


async def test_records_rounded_to_two_decimals(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    fake_api.tables = [FakeTable([FakeRecord({"_time": START, "_value": 120.62696046662376})])]
    points = await repo.instant_series(Variable.VOLTAGE_A, START, STOP, HOUR)
    assert points[0].value == 120.63


async def test_last_value_returns_most_recent_point(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    fake_api.tables = [
        FakeTable(
            [
                FakeRecord({"_time": START, "_value": 100.0}),
                FakeRecord({"_time": START + HOUR, "_value": 105.0}),
            ]
        )
    ]
    point = await repo.last_value(Variable.POWER_ACTIVE_INST_TOTAL, device_id="11")
    assert point is not None
    assert point.value == 105.0
    assert point.time == START + HOUR
    assert fake_api.flux is not None
    assert "|> last()" in fake_api.flux
    assert "r.identify_device == _device_id" in fake_api.flux


async def test_last_value_none_when_empty(repo: InfluxRepository) -> None:
    assert await repo.last_value(Variable.VOLTAGE_A) is None


async def test_list_device_ids_sorted_and_deduplicated(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    fake_api.tables = [
        FakeTable([FakeRecord({"_value": "11"}), FakeRecord({"_value": None})]),
        FakeTable([FakeRecord({"_value": "2"})]),
    ]
    device_ids = await repo.list_device_ids()
    assert device_ids == ["11", "2"]  # sort() es lexicográfico, no numérico
    assert fake_api.flux is not None
    assert "schema.tagValues" in fake_api.flux


async def test_list_device_ids_empty(repo: InfluxRepository) -> None:
    assert await repo.list_device_ids() == []


async def test_instant_series_offset_matches_configured_timezone(fake_api: FakeQueryApi) -> None:
    """Regresión: aggregateWindow() alinea sus ventanas a UTC por defecto;
    sin `offset`, una ventana diaria "muerde" 5h del día anterior en hora
    Bogotá (UTC-5) — ver flux_window_offset()."""
    bogota_repo = InfluxRepository(fake_api, "modbus_data_v2", "America/Bogota")  # pyright: ignore[reportArgumentType]
    await bogota_repo.instant_series(Variable.VOLTAGE_A, START, STOP, HOUR)
    assert fake_api.params is not None
    assert fake_api.params["_offset"] == timedelta(hours=5)
    assert "offset: _offset" in (fake_api.flux or "")


async def test_energy_series_offset_matches_configured_timezone(fake_api: FakeQueryApi) -> None:
    bogota_repo = InfluxRepository(fake_api, "modbus_data_v2", "America/Bogota")  # pyright: ignore[reportArgumentType]
    await bogota_repo.energy_series(Variable.POWER_ACTIVE_TOTAL_POS, START, STOP, HOUR)
    assert fake_api.params is not None
    assert fake_api.params["_offset"] == timedelta(hours=5)


async def test_offset_is_zero_for_utc() -> None:
    assert flux_window_offset("UTC", START) == timedelta(0)


async def test_offset_is_noop_for_divisor_windows() -> None:
    """5h % 1h == 0: el offset no cambia los límites de una ventana horaria
    — solo importa (y corrige) para ventanas de 1 día o más."""
    offset = flux_window_offset("America/Bogota", START)
    assert offset % HOUR == timedelta(0)
