"""F1.3 — la carga base ("siempre encendido") y lo que cuesta al mes.

Percentil, no mínimo: un mínimo instantáneo lo fija cualquier hueco entre dos
ciclos de una nevera. Y la ventana depende del modo de la sede: con generación
el percentil del día completo daría un negativo, porque de día el medidor solo
ve el balance neto.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.cache import clear_all_caches
from app.schemas.influx import TimeSeriesPoint
from app.schemas.tariff import TariffConfig, TariffPeriod
from app.services.analytics.baseload import baseload_trend
from app.services.crm.fleet import ClientFleet, FleetDevice
from tests.conftest import TEST_CLIENT_ID, TEST_DEVICE_ID, FakeFleetDirectory
from tests.fakes import FakeInfluxRepository

START = datetime(2026, 8, 10, 5, tzinfo=UTC)  # medianoche en Bogotá
STOP = datetime(2026, 8, 13, 5, tzinfo=UTC)
TZ = "America/Bogota"
SIN_TARIFA = TariffConfig()
CON_TARIFA = TariffConfig(
    periods=[TariffPeriod(month="2026-08", cu_cop_kwh=900.0, excedente_cop_kwh=110.0)]
)


@pytest.fixture(autouse=True)
def _sin_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    clear_all_caches()


def _dia(fecha: datetime, valores_por_hora: dict[int, float]) -> list[TimeSeriesPoint]:
    """Una muestra por hora local del día dado (la hora 0 local = 05:00 UTC)."""
    return [
        TimeSeriesPoint(time=fecha + timedelta(hours=hora), value=valor)
        for hora, valor in valores_por_hora.items()
    ]


class TestSinGeneracion:
    async def test_usa_el_dia_completo(self) -> None:
        repo = FakeInfluxRepository()
        # Piso de 200 W toda la noche y picos de día: la carga base es el piso.
        repo.instant_series_points = _dia(
            START, {0: 200.0, 1: 200.0, 2: 210.0, 12: 3000.0, 19: 4500.0}
        )

        result = await baseload_trend(
            repo, START, STOP, TEST_DEVICE_ID, TZ, "consumo", SIN_TARIFA, percentile=0.05
        )

        assert result.window == "dia"
        assert result.points[0].base_load_w == 200.0
        assert result.current_w == 200.0

    async def test_traduce_la_carga_base_a_kwh_y_pesos_del_mes(self) -> None:
        repo = FakeInfluxRepository()
        repo.instant_series_points = _dia(START, {0: 200.0, 1: 200.0, 12: 1000.0})

        result = await baseload_trend(
            repo, START, STOP, TEST_DEVICE_ID, TZ, "consumo", CON_TARIFA
        )

        # 200 W x 24 h x 30 días = 144 kWh al mes.
        assert result.monthly_kwh == 144.0
        assert result.monthly_cost_cop == round(144.0 * 900.0, 2)

    async def test_sin_tarifa_registrada_no_inventa_el_costo(self) -> None:
        repo = FakeInfluxRepository()
        repo.instant_series_points = _dia(START, {0: 200.0, 1: 200.0})

        result = await baseload_trend(
            repo, START, STOP, TEST_DEVICE_ID, TZ, "consumo", SIN_TARIFA
        )

        assert result.monthly_kwh == 144.0
        assert result.monthly_cost_cop is None


class TestConGeneracion:
    async def test_solo_mira_la_ventana_nocturna(self) -> None:
        """De día el medidor ve el balance neto: incluir esas horas metería la
        generación dentro de la "carga base"."""
        repo = FakeInfluxRepository()
        repo.instant_series_points = _dia(
            START,
            # Noche importando 300 W; de día exportando (negativo) y un pico
            # bajo de importación a las 12 que NO debe fijar la carga base.
            {0: 300.0, 1: 310.0, 2: 305.0, 12: -2000.0, 13: 50.0},
        )

        result = await baseload_trend(
            repo, START, STOP, TEST_DEVICE_ID, TZ, "generacion", SIN_TARIFA, percentile=0.05
        )

        assert result.window == "noche"
        assert result.points[0].base_load_w == 300.0

    async def test_sin_muestras_nocturnas_no_hay_carga_base(self) -> None:
        repo = FakeInfluxRepository()
        repo.instant_series_points = _dia(START, {12: -2000.0, 13: 100.0})

        result = await baseload_trend(
            repo, START, STOP, TEST_DEVICE_ID, TZ, "generacion", SIN_TARIFA
        )

        assert result.points == []
        assert result.current_w is None
        assert result.monthly_cost_cop is None


class TestLaTendencia:
    async def test_detecta_que_la_carga_base_subio(self) -> None:
        """Algo que se queda encendido sube el piso y ya no baja: la mediana de
        los últimos 7 días contra la de los 7 anteriores lo muestra."""
        repo = FakeInfluxRepository()
        puntos: list[TimeSeriesPoint] = []
        for dia in range(14):
            base = 120.0 if dia < 7 else 190.0
            inicio = START + timedelta(days=dia)
            puntos.extend(_dia(inicio, {0: base, 1: base + 5, 2: base + 10}))
        repo.instant_series_points = puntos

        result = await baseload_trend(
            repo, START, START + timedelta(days=14), TEST_DEVICE_ID, TZ, "consumo", SIN_TARIFA
        )

        assert result.current_w == 190.0
        assert result.trend_delta_w == 70.0

    async def test_estable_no_reporta_tendencia(self) -> None:
        repo = FakeInfluxRepository()
        puntos: list[TimeSeriesPoint] = []
        for dia in range(14):
            puntos.extend(_dia(START + timedelta(days=dia), {0: 150.0, 1: 155.0}))
        repo.instant_series_points = puntos

        result = await baseload_trend(
            repo, START, START + timedelta(days=14), TEST_DEVICE_ID, TZ, "consumo", SIN_TARIFA
        )

        assert result.trend_delta_w == 0.0


class TestElEndpoint:
    def test_la_ventana_sale_del_modo_de_la_sede(
        self,
        app: FastAPI,
        client: TestClient,
        fleet: ClientFleet,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        app.state.fleet_directory = FakeFleetDirectory(_fleet_declarada(True, fleet))
        fake_influx_repo.instant_series_points = _dia(START, {0: 300.0, 1: 310.0})

        response = client.get(
            "/api/v1/analytics/baseload-trend",
            params={"from": START.isoformat(), "to": STOP.isoformat()},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["window"] == "noche"

    def test_una_sede_de_consumo_mide_el_dia_completo(
        self,
        app: FastAPI,
        client: TestClient,
        fleet: ClientFleet,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        app.state.fleet_directory = FakeFleetDirectory(_fleet_declarada(False, fleet))
        fake_influx_repo.instant_series_points = _dia(START, {12: 500.0, 13: 520.0})

        response = client.get(
            "/api/v1/analytics/baseload-trend",
            params={"from": START.isoformat(), "to": STOP.isoformat()},
            headers=auth_headers,
        )

        data = response.json()["data"]
        assert data["window"] == "dia"
        assert data["current_w"] == 500.0

    def test_requiere_autenticacion(self, client: TestClient) -> None:
        assert client.get("/api/v1/analytics/baseload-trend").status_code == 401


def _fleet_declarada(tiene_generacion: bool, fleet: ClientFleet) -> ClientFleet:
    device = FleetDevice(
        id=TEST_DEVICE_ID,
        nombre="Medidor de prueba",
        modbus_id=1,
        sede_id="sede-1",
        sede="Planta Norte",
        gateway_id="gw-1",
        gateway="GW-0001",
        gateway_en_linea=True,
        tiene_generacion=tiene_generacion,
    )
    return ClientFleet(
        client_id=TEST_CLIENT_ID,
        devices=(device,),
        variables=fleet.variables,
        puede_ver_consumo=True,
    )


async def test_una_muestra_negativa_no_cuenta_como_carga() -> None:
    """Exportar no es consumo de fondo: si una muestra negativa entrara al
    percentil, la carga base de una sede solar sería negativa."""
    repo = FakeInfluxRepository()
    repo.instant_series_points = _dia(START, {0: -500.0, 1: 200.0, 2: 220.0})

    result = await baseload_trend(
        repo, START, STOP, TEST_DEVICE_ID, TZ, "consumo", SIN_TARIFA
    )

    assert result.points[0].base_load_w == 200.0
    assert result.points[0].sample_count == 2
