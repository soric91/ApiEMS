"""F1.2 — la proyección de la factura del mes.

Lo que hay que proteger: que no proyecte sin historial suficiente, que separe
tipos de día (un lunes no se parece a un domingo), que la banda salga de los
días reales, y que el costo lo calcule el MISMO motor de tarifa que factura
/costs y /reports.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.cache import clear_all_caches
from app.core.config import Settings
from app.models.variables import Variable
from app.schemas.influx import EnergyPoint
from app.schemas.tariff import TariffConfig, TariffPeriod
from app.services.forecast.bill import LOOKBACK_DAYS, bill_forecast
from tests.fakes import FakeInfluxRepository

# 15 de agosto de 2026 a las 12:00 Bogotá (17:00 UTC): medio mes transcurrido.
AHORA = datetime(2026, 8, 15, 17, tzinfo=UTC)
TZ = "America/Bogota"
SIN_TARIFA = TariffConfig()
CON_TARIFA = TariffConfig(
    periods=[TariffPeriod(month="2026-08", cu_cop_kwh=900.0, excedente_cop_kwh=110.0)]
)


@pytest.fixture(autouse=True)
def _sin_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    clear_all_caches()


def _settings() -> Settings:
    return Settings(_env_file=None, TIMEZONE=TZ)  # pyright: ignore[reportCallIssue]


def _dias(valores: list[float]) -> list[EnergyPoint]:
    """Un punto diario terminando ayer, en orden cronológico."""
    primer_dia = datetime(2026, 8, 15, 5, tzinfo=UTC) - timedelta(days=len(valores))
    return [
        EnergyPoint(time=primer_dia + timedelta(days=i), value=valor)
        for i, valor in enumerate(valores)
    ]


class TestSinHistorialSuficiente:
    async def test_no_proyecta_con_pocos_dias(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias([10.0] * 5),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }
        repo.energy_total_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: 50.0,
            Variable.POWER_ACTIVE_TOTAL_NEG: 0.0,
        }

        result = await bill_forecast(repo, _settings(), CON_TARIFA, None, AHORA)

        assert result.method == "insufficient_history"
        assert result.kwh_projected is None
        assert result.cost_projected_cop is None
        # Lo que va del mes sí se informa: eso es un dato, no una proyección.
        assert result.kwh_mtd == 50.0
        assert result.days_total == 31


class TestLaProyeccion:
    async def test_a_ritmo_constante_termina_en_el_consumo_diario_por_los_dias(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias([10.0] * LOOKBACK_DAYS),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }
        repo.energy_total_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: 145.0,
            Variable.POWER_ACTIVE_TOTAL_NEG: 0.0,
        }

        result = await bill_forecast(repo, _settings(), SIN_TARIFA, None, AHORA)

        assert result.method == "ewma_por_tipo_de_dia"
        # 145 kWh hasta el mediodía del 15 + 16 días completos que faltan (del
        # 16 al 31) x 10 kWh + la mitad del 15 que aún no pasó.
        assert result.kwh_projected == 310.0

    async def test_lo_reciente_pesa_mas_que_lo_viejo(self) -> None:
        """Si el consumo se duplicó la última semana, la proyección tiene que
        moverse hacia el ritmo nuevo, no quedarse en el promedio del mes."""
        repo = FakeInfluxRepository()
        historico = [5.0] * (LOOKBACK_DAYS - 7) + [20.0] * 7
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias(historico),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }
        repo.energy_total_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: 100.0,
            Variable.POWER_ACTIVE_TOTAL_NEG: 0.0,
        }

        result = await bill_forecast(repo, _settings(), SIN_TARIFA, None, AHORA)

        assert result.kwh_projected is not None
        media_diaria_restante = (result.kwh_projected - 100.0) / 16.5
        promedio_simple = sum(historico) / len(historico)
        assert media_diaria_restante > promedio_simple

    async def test_la_banda_sale_de_los_dias_reales(self) -> None:
        repo = FakeInfluxRepository()
        # Días entre 8 y 12 kWh: la banda tiene que abrirse alrededor de eso.
        historico = [8.0, 12.0] * (LOOKBACK_DAYS // 2)
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias(historico),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }
        repo.energy_total_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: 150.0,
            Variable.POWER_ACTIVE_TOTAL_NEG: 0.0,
        }

        result = await bill_forecast(repo, _settings(), SIN_TARIFA, None, AHORA)

        assert result.kwh_p10 is not None
        assert result.kwh_p90 is not None
        assert result.kwh_projected is not None
        assert result.kwh_p10 < result.kwh_projected < result.kwh_p90
        # Ninguna proyección puede quedar por debajo de lo que YA se consumió.
        assert result.kwh_p10 > 150.0


class TestElCosto:
    async def test_lo_calcula_el_motor_de_tarifa_de_siempre(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias([10.0] * LOOKBACK_DAYS),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }
        repo.energy_total_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: 145.0,
            Variable.POWER_ACTIVE_TOTAL_NEG: 0.0,
        }

        result = await bill_forecast(repo, _settings(), CON_TARIFA, None, AHORA)

        assert result.kwh_projected == 310.0
        assert result.cost_projected_cop == round(310.0 * 900.0, 2)

    async def test_sin_tarifa_del_mes_el_costo_es_cero_pero_los_kwh_no(self) -> None:
        """Igual que /costs: sin tarifa registrada no se inventa un precio."""
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias([10.0] * LOOKBACK_DAYS),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }
        repo.energy_total_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: 145.0,
            Variable.POWER_ACTIVE_TOTAL_NEG: 0.0,
        }

        result = await bill_forecast(repo, _settings(), SIN_TARIFA, None, AHORA)

        assert result.cost_projected_cop == 0.0
        assert result.kwh_projected == 310.0

    async def test_la_exportacion_proyectada_baja_el_costo(self) -> None:
        """Una sede que exporta paga menos: el crédito entra por el mismo motor
        de dos tramos que la factura real."""
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias([10.0] * LOOKBACK_DAYS),
            Variable.POWER_ACTIVE_TOTAL_NEG: _dias([4.0] * LOOKBACK_DAYS),
        }
        repo.energy_total_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: 145.0,
            Variable.POWER_ACTIVE_TOTAL_NEG: 60.0,
        }

        result = await bill_forecast(repo, _settings(), CON_TARIFA, None, AHORA)

        assert result.export_projected_kwh is not None
        assert result.export_projected_kwh > 60.0
        assert result.cost_projected_cop is not None
        assert result.cost_projected_cop < round(310.0 * 900.0, 2)


class TestElEndpoint:
    def test_devuelve_la_proyeccion(
        self,
        client: TestClient,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        fake_influx_repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias([10.0] * LOOKBACK_DAYS),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }

        response = client.get("/api/v1/forecast/bill", headers=auth_headers)

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["method"] in {"ewma_por_tipo_de_dia", "insufficient_history"}
        assert data["days_total"] in {28, 29, 30, 31}

    def test_requiere_autenticacion(self, client: TestClient) -> None:
        assert client.get("/api/v1/forecast/bill").status_code == 401
