from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.models.variables import (
    REACTIVE_QUADRANTS,
    Aggregation,
    InvalidAggregationError,
    Variable,
)
from app.repositories.influx import VENTANA_BASE, InfluxRepository
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
    """Captura la consulta Flux y sus params; devuelve las tablas configuradas.

    `respuestas` sirve para los métodos que hacen MÁS de una consulta —la
    energía de un rango pide primero su base anterior y después el cierre—: se
    devuelve una por llamada, en orden.
    """

    def __init__(self) -> None:
        self.flux: str | None = None
        self.params: dict[str, Any] | None = None
        self.tables: list[FakeTable] = []
        self.respuestas: list[list[FakeTable]] | None = None
        self.consultas: list[tuple[str, dict[str, Any]]] = []

    async def query(self, flux: str, params: dict[str, Any]) -> list[Any]:
        self.flux = flux
        self.params = params
        self.consultas.append((flux, params))
        if self.respuestas is not None:
            return self.respuestas.pop(0) if self.respuestas else []
        return self.tables

    async def query_stream(self, flux: str, params: dict[str, Any] | None = None) -> Any:
        # Igual que el cliente real: devolver un AsyncGenerator ya construido.
        self.flux = flux
        self.params = params
        return self._stream()

    def _stream(self) -> Any:
        async def _iter() -> Any:
            for table in self.tables:
                for record in table.records:
                    yield record

        return _iter()


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


async def test_energy_records_streams_raw_points(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """energy_records vuelca TODO lo que reportó el contador, sin agregar nada:
    el volcado a CSV se hace fila por fila (streaming), no en una lista gigante."""
    fake_api.tables = [
        FakeTable(
            [
                FakeRecord(
                    {
                        "_time": START,
                        "_field": "Q1Eq",
                        "_value": 12.506,
                        "identify_device": "d-01",
                    }
                ),
                FakeRecord(
                    {
                        "_time": START + timedelta(seconds=1),
                        "_field": "Q2Eq",
                        "_value": 3.0,
                        "identify_device": "d-01",
                    }
                ),
            ]
        )
    ]

    records = await repo.energy_records(REACTIVE_QUADRANTS, START, STOP, device_id="11")
    out = [(time, device, field, value) async for time, device, field, value in records]

    assert out == [
        (START, "d-01", "Q1Eq", 12.51),  # redondeado a 2 decimales, como el resto
        (START + timedelta(seconds=1), "d-01", "Q2Eq", 3.0),
    ]
    assert fake_api.flux is not None
    assert "contains(value: r._field, set: _fields)" in fake_api.flux
    assert "aggregateWindow" not in fake_api.flux
    assert "spread()" not in fake_api.flux
    assert fake_api.params is not None
    assert fake_api.params["_fields"] == ["Q1Eq", "Q2Eq", "Q3Eq", "Q4Eq"]
    assert fake_api.params["_device_id"] == "11"


async def test_energy_records_scopes_by_device_set(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    await repo.energy_records(REACTIVE_QUADRANTS, START, STOP, devices=("d-1", "d-2"))
    assert fake_api.flux is not None
    assert "contains(value: r.identify_device, set: _devices)" in fake_api.flux
    assert fake_api.params is not None
    assert fake_api.params["_devices"] == ["d-1", "d-2"]


async def test_energy_records_rejects_instant_variable(repo: InfluxRepository) -> None:
    with pytest.raises(ValueError, match="no es un contador"):
        await repo.energy_records((Variable.VOLTAGE_A,), START, STOP)


async def test_energy_records_skips_records_without_value(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """El stream puede traer filas con _value nulo (p. ej. cierres de tabla);
    no son un punto y no deben aparecer en el CSV."""
    fake_api.tables = [
        FakeTable(
            [
                FakeRecord({"_time": START, "_field": "Q1Eq", "_value": None}),
                FakeRecord(
                    {"_time": START + timedelta(seconds=1), "_field": "Q1Eq", "_value": 1.5}
                ),
            ]
        )
    ]

    records = await repo.energy_records((Variable.POWER_REACTIVE_QUAD1,), START, STOP)
    out = [(t, d, f, v) async for t, d, f, v in records]

    assert out == [(START + timedelta(seconds=1), "", "Q1Eq", 1.5)]


async def test_offset_is_noop_for_divisor_windows() -> None:
    """5h % 1h == 0: el offset no cambia los límites de una ventana horaria
    — solo importa (y corrige) para ventanas de 1 día o más."""
    offset = flux_window_offset("America/Bogota", START)
    assert offset % HOUR == timedelta(0)


async def test_energy_total_de_rango_vacio_vale_cero(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """Un rango que no abarca nada vale cero, y ni siquiera se consulta.

    InfluxDB responde 400 ("cannot query an empty range") y eso salía como un
    500. No lo dispara un rango mal escrito sino una ventana DERIVADA que
    colapsa: `compute_kpis` recorta "lo que va del día" contra el `stop`
    pedido, y con un `stop` en la medianoche local ese tramo no dura nada.
    """
    total = await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, STOP, STOP)

    assert total == 0.0
    assert fake_api.flux is None


async def test_energy_total_de_rango_invertido_tampoco_consulta(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    total = await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, STOP, START)

    assert total == 0.0
    assert fake_api.flux is None


async def test_totales_por_contador_de_rango_vacio_dan_cero(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    totales = await repo.energy_totals_by_counter(list(REACTIVE_QUADRANTS), STOP, STOP)

    assert totales == dict.fromkeys(REACTIVE_QUADRANTS, 0.0)
    assert fake_api.flux is None


async def test_series_por_contador_de_rango_vacio_dan_series_vacias(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    series = await repo.energy_series_by_counter(list(REACTIVE_QUADRANTS), STOP, STOP, HOUR)

    assert series == {c: [] for c in REACTIVE_QUADRANTS}
    assert fake_api.flux is None


async def test_registros_de_rango_vacio_no_traen_nada(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    stream = await repo.energy_records(list(REACTIVE_QUADRANTS), STOP, STOP)

    assert [record async for record in stream] == []
    assert fake_api.flux is None


async def test_rango_vacio_sigue_rechazando_una_variable_instantanea(
    repo: InfluxRepository,
) -> None:
    # La guarda del rango no puede tapar el error de uso: un voltaje no es un
    # contador acumulativo, con rango vacío o sin él.
    with pytest.raises(ValueError, match="no es un contador"):
        await repo.energy_total(Variable.VOLTAGE_A, STOP, STOP)


# ---------------------------------------------------------------------------
# El pico que aparece después de un vacío de datos
#
# Caso real (2026-08-09/10): el medidor dejó de reportar a las 19:00 y volvió a
# las 02:21. La ventana siguiente trajo 5,04 kWh, que a primera vista es
# imposible: en un minuto, a 686 W, corresponden 0,011 kWh.
#
# Estos tests auditan las cuatro decisiones de `energy_series` de las que
# depende que ese valor sea energía REAL mal fechada y no energía inventada. Si
# alguna cambia, el número deja de poder justificarse y el test cae.
# ---------------------------------------------------------------------------


async def test_energia_por_ventana_usa_last_y_diferencia_no_negativa(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """La energía sale de restar el contador, no de promediarlo.

    `fn: last` toma el valor del contador al cerrar cada ventana y
    `difference()` resta contra el cierre de la ventana anterior: por eso el
    resultado es exactamente lo que avanzó el contador entre dos instantes, sin
    importar cuántas muestras hubo en el medio ni si hubo alguna.
    """
    await repo.energy_series(Variable.POWER_ACTIVE_TOTAL_POS, START, STOP, HOUR)

    assert fake_api.flux is not None
    assert "fn: last" in fake_api.flux
    assert "difference(nonNegative: true)" in fake_api.flux
    # Un promedio o una suma sobre un contador acumulativo darían un número sin
    # significado físico.
    assert "fn: mean" not in fake_api.flux
    assert "sum()" not in fake_api.flux


async def test_el_rango_se_extiende_una_ventana_hacia_atras(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """Sin ese margen, la primera ventana pedida no tendría contra qué restar.

    Es la razón por la que `_start` va antes del `from` del usuario: la resta
    necesita el cierre de la ventana anterior, que está fuera del rango pedido.
    """
    await repo.energy_series(Variable.POWER_ACTIVE_TOTAL_POS, START, STOP, HOUR)

    assert fake_api.params is not None
    assert fake_api.params["_start"] == START - HOUR
    assert fake_api.params["_stop"] == STOP


async def test_el_punto_del_borde_no_se_cuenta_dos_veces(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """La garantía de que trocear un rango no infla el total.

    El histórico fino pide el rango de a pedazos. Si el punto que cae justo en
    `start` se emitiera, aparecería también como último punto del tramo
    anterior y su energía se contaría dos veces. Se descarta por `t > start`.
    """
    fake_api.tables = [
        FakeTable(
            [
                FakeRecord({"_time": START, "_value": 4.0}),  # cierre del tramo anterior
                FakeRecord({"_time": START + HOUR, "_value": 1.0}),
                FakeRecord({"_time": STOP, "_value": 2.0}),
            ]
        )
    ]

    puntos = await repo.energy_series(Variable.POWER_ACTIVE_TOTAL_POS, START, STOP, HOUR)

    assert [p.time for p in puntos] == [START + HOUR, STOP]
    assert sum(p.value for p in puntos) == 3.0


async def test_la_energia_de_un_vacio_no_se_reparte_ni_se_pierde(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """El pico de después del vacío es lo que avanzó el contador, entero.

    InfluxDB no crea ventanas vacías (`createEmpty: false`), así que las horas
    sin lecturas no existen como puntos y la resta que las cruza abarca todo el
    silencio. El total del rango sigue siendo correcto: la energía está toda,
    concentrada en un punto que dice durar menos de lo que abarca.
    """
    # Siete horas de silencio entre dos lecturas, y después el ritmo normal.
    fake_api.tables = [
        FakeTable(
            [
                FakeRecord({"_time": START, "_value": 0.0}),
                FakeRecord({"_time": START + timedelta(hours=7), "_value": 5.04}),
                FakeRecord({"_time": START + timedelta(hours=8), "_value": 0.68}),
            ]
        )
    ]

    puntos = await repo.energy_series(
        Variable.POWER_ACTIVE_TOTAL_POS, START, START + timedelta(hours=8), HOUR
    )

    # Ni se reparte entre las horas ausentes (sería inventar una curva que nadie
    # midió) ni se descarta (dejaría el total por debajo del contador).
    assert [p.value for p in puntos] == [5.04, 0.68]
    assert sum(p.value for p in puntos) == 5.72


@pytest.mark.parametrize("ventana", [timedelta(seconds=1), timedelta(minutes=1), HOUR])
async def test_el_tamano_de_ventana_no_cambia_el_total(
    repo: InfluxRepository, fake_api: FakeQueryApi, ventana: timedelta
) -> None:
    """La invariante que hace auditable a todo lo demás.

    Lo que el contador avanzó entre dos instantes es un hecho del medidor y no
    depende de con qué lupa se lo mire. Comprobado también contra los datos
    reales del cliente: el mismo rango dio 8,55 kWh a un minuto y 8,55 kWh a
    cinco; el pico solo cambia de ventana, no de tamaño.
    """
    fake_api.tables = [
        FakeTable(
            [
                FakeRecord({"_time": START, "_value": 0.0}),
                FakeRecord({"_time": START + ventana, "_value": 2.5}),
                FakeRecord({"_time": START + ventana * 2, "_value": 1.5}),
            ]
        )
    ]

    puntos = await repo.energy_series(
        Variable.POWER_ACTIVE_TOTAL_POS, START, START + ventana * 2, ventana
    )

    assert sum(p.value for p in puntos) == 4.0


# ---------------------------------------------------------------------------
# La energía que se perdía en el borde del periodo
#
# Caso real (2026-08-09/10): el medidor calló de 19:00 a 02:21. Consultando
# desde el 9 aparecían 5,04 kWh en un punto; consultando desde el 10 —que es lo
# que hace el reporte diario, que arranca a medianoche— no aparecían en ningún
# lado. La misma energía, registrada por el contador, daba dos respuestas según
# dónde cayera el límite del rango.
# ---------------------------------------------------------------------------

MEDIANOCHE = datetime(2026, 8, 10, 5, tzinfo=UTC)  # 00:00 en Bogotá
FIN_DEL_DIA = MEDIANOCHE + timedelta(days=1)


def _tabla(*valores: tuple[datetime, float]) -> list[FakeTable]:
    return [FakeTable([FakeRecord({"_time": t, "_value": v}) for t, v in valores])]


async def test_el_total_arranca_de_la_lectura_anterior_al_rango(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """Lo consumido durante el silencio inicial entra en el total.

    El contador marcaba 100 a las 19:00 —última lectura antes del corte— y 105,04
    al reconectar. Esos 5,04 kWh se consumieron de verdad; con `spread()` sobre
    el rango se perdían porque la primera muestra de adentro ya era la de
    después del corte.
    """
    fake_api.respuestas = [
        _tabla((MEDIANOCHE - timedelta(hours=5), 100.0)),  # base: 19:00 del día anterior
        _tabla((FIN_DEL_DIA - timedelta(minutes=1), 105.04)),  # cierre del día
    ]

    total = await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, MEDIANOCHE, FIN_DEL_DIA)

    assert total == 5.04


async def test_la_base_se_busca_antes_del_rango_y_el_cierre_dentro(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """Dos consultas, y cada una mira donde debe."""
    fake_api.respuestas = [
        _tabla((MEDIANOCHE - timedelta(hours=5), 100.0)),
        _tabla((FIN_DEL_DIA - timedelta(minutes=1), 105.04)),
    ]

    await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, MEDIANOCHE, FIN_DEL_DIA)

    base, cierre = fake_api.consultas
    assert base[1]["_start"] == MEDIANOCHE - VENTANA_BASE
    assert base[1]["_stop"] == MEDIANOCHE
    assert cierre[1]["_start"] == MEDIANOCHE
    assert cierre[1]["_stop"] == FIN_DEL_DIA
    # `last()` en las dos: el contador al cerrar cada tramo, no un promedio.
    assert "last()" in base[0]
    assert "last()" in cierre[0]


async def test_sin_lectura_previa_se_usa_la_resta_de_siempre(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """Un medidor recién instalado no tiene base que buscar.

    Ahí la primera muestra del rango es lo mejor que hay, y `spread()` sigue
    siendo la respuesta correcta.
    """
    fake_api.respuestas = [
        [],  # sin lectura anterior
        # spread() lo resuelve InfluxDB: devuelve una fila con la diferencia ya
        # hecha, no las muestras.
        _tabla((FIN_DEL_DIA, 2.5)),
    ]

    total = await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, MEDIANOCHE, FIN_DEL_DIA)

    assert total == 2.5
    assert "spread()" in fake_api.consultas[-1][0]


async def test_un_periodo_entero_en_silencio_no_inventa_energia(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """Si el medidor no habló en todo el rango, su energía todavía no se sabe.

    Se conocerá cuando vuelva: el contador la traerá acumulada. Dar por bueno
    el salto ahora la atribuiría a un periodo que quizá ni siquiera terminó.
    """
    fake_api.respuestas = [
        _tabla((MEDIANOCHE - timedelta(hours=5), 100.0)),
        [],  # ni una lectura dentro del rango
    ]

    total = await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, MEDIANOCHE, FIN_DEL_DIA)

    assert total == 0.0


async def test_un_contador_que_retrocede_no_da_energia_negativa(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    # Un reinicio del equipo o una lectura corrupta: energía importada negativa
    # no existe.
    fake_api.respuestas = [
        _tabla((MEDIANOCHE - timedelta(hours=1), 500.0)),
        _tabla((FIN_DEL_DIA, 12.0)),
    ]

    total = await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, MEDIANOCHE, FIN_DEL_DIA)

    assert total == 0.0


async def test_dos_dias_seguidos_suman_lo_que_avanzo_el_contador(
    repo: InfluxRepository, fake_api: FakeQueryApi
) -> None:
    """La invariante que hace que el mes cuadre con el medidor.

    Con `spread()`, un corte sobre la medianoche dejaba a los dos días por
    debajo y al mes le faltaba la diferencia. Ahora cada día arranca donde
    terminó el anterior, así que la suma de los días es lo que marcó el
    contador — aunque el corte haga que un día cargue con lo del otro.
    """
    ayer, hoy = MEDIANOCHE - timedelta(days=1), MEDIANOCHE

    fake_api.respuestas = [
        _tabla((ayer - timedelta(hours=1), 100.0)),
        _tabla((ayer + timedelta(hours=19), 103.0)),
    ]
    total_ayer = await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, ayer, hoy)

    fake_api.respuestas = [
        _tabla((ayer + timedelta(hours=19), 103.0)),
        _tabla((hoy + timedelta(hours=23), 111.54)),
    ]
    total_hoy = await repo.energy_total(Variable.POWER_ACTIVE_TOTAL_POS, hoy, FIN_DEL_DIA)

    # 100 -> 111,54 en el contador; los dos días suman exactamente eso.
    assert round(total_ayer + total_hoy, 2) == 11.54
