"""F3.1 — el pronóstico de consumo por hora.

Sin modelo entrenado: la media exponencial de esa misma hora en días del mismo
tipo. Lo que hay que proteger es la honestidad de la cifra — que no pronostique
sin historial, que la banda salga de días reales, y que el backtest se mida
sobre datos que NO entraron en el cálculo.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.cache import clear_all_caches
from app.core.config import Settings
from app.models.variables import Variable
from app.schemas.influx import EnergyPoint
from app.services.forecast.power import MIN_HISTORY_DAYS, power_forecast
from tests.fakes import FakeInfluxRepository

TZ = "America/Bogota"
# Viernes 14 de agosto de 2026, 10:00 Bogotá (15:00 UTC).
AHORA = datetime(2026, 8, 14, 15, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _sin_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    clear_all_caches()


def _settings() -> Settings:
    return Settings(_env_file=None, TIMEZONE=TZ)  # pyright: ignore[reportCallIssue]


def _historia(dias: int, perfil: dict[int, float], base: float = 0.2) -> list[EnergyPoint]:
    """Una serie horaria de `dias` días terminando en AHORA.

    `perfil` fija el consumo de ciertas horas LOCALES; el resto vale `base`.
    """
    inicio = (AHORA.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)) - timedelta(
        days=dias
    )
    puntos: list[EnergyPoint] = []
    momento = inicio
    while momento < AHORA:
        hora_local = (momento - timedelta(hours=5)).hour  # Bogotá = UTC-5
        puntos.append(EnergyPoint(time=momento, value=perfil.get(hora_local, base)))
        momento += timedelta(hours=1)
    return puntos


class TestSinHistorialSuficiente:
    async def test_no_pronostica(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _historia(MIN_HISTORY_DAYS - 2, {}),
        }

        result = await power_forecast(repo, _settings(), None, 24, AHORA)

        assert result.method == "insufficient_history"
        assert result.points == []
        assert result.backtest is None


class TestElPronostico:
    async def test_repite_el_patron_de_esa_hora(self) -> None:
        """Si todos los días se consume 3 kWh a las 19:00, eso es lo que se
        espera mañana a las 19:00."""
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _historia(28, {19: 3.0}),
        }

        result = await power_forecast(repo, _settings(), None, 24, AHORA)

        assert result.method == "ewma_por_tipo_de_dia_y_hora"
        assert result.target == "import_kwh"
        de_las_19 = [p for p in result.points if (p.time - timedelta(hours=5)).hour == 19]
        assert de_las_19
        assert all(abs(p.kwh - 3.0) < 0.01 for p in de_las_19)

    async def test_arranca_en_la_proxima_hora_en_punto(self) -> None:
        """La hora en curso está a medio consumir: pronosticarla entera daría
        un número que ya no se puede cumplir."""
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _historia(28, {}),
        }

        result = await power_forecast(repo, _settings(), None, 6, AHORA)

        assert result.points[0].time == AHORA.replace(minute=0) + timedelta(hours=1)

    async def test_la_banda_encierra_lo_esperado(self) -> None:
        repo = FakeInfluxRepository()
        # La misma hora oscila entre 2 y 4 kWh según el día.
        puntos = _historia(28, {19: 3.0})
        for i, punto in enumerate(puntos):
            if (punto.time - timedelta(hours=5)).hour == 19:
                puntos[i] = EnergyPoint(time=punto.time, value=2.0 if i % 2 == 0 else 4.0)
        repo.energy_series_points_by_counter = {Variable.POWER_ACTIVE_TOTAL_POS: puntos}

        result = await power_forecast(repo, _settings(), None, 24, AHORA)

        de_las_19 = next(p for p in result.points if (p.time - timedelta(hours=5)).hour == 19)
        assert de_las_19.p10 <= de_las_19.kwh <= de_las_19.p90
        assert de_las_19.p10 >= 2.0
        assert de_las_19.p90 <= 4.0

    async def test_el_horizonte_se_respeta(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _historia(28, {}),
        }

        result = await power_forecast(repo, _settings(), None, 12, AHORA)

        assert len(result.points) == 12


class TestElBacktest:
    async def test_mide_contra_el_ingenuo(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _historia(28, {19: 3.0, 7: 1.5}),
        }

        result = await power_forecast(repo, _settings(), None, 24, AHORA)

        assert result.backtest is not None
        assert result.backtest.hours > 0
        # Con un patrón perfectamente repetido los dos aciertan; lo que importa
        # es que el número exista y sea comparable.
        assert result.backtest.mae_kwh <= result.backtest.naive_mae_kwh + 0.01

    async def test_gana_al_ingenuo_cuando_hay_un_dia_raro(self) -> None:
        """El ingenuo copia el día anterior, así que un día atípico le arruina
        el siguiente; la media de varios días del mismo tipo lo absorbe."""
        repo = FakeInfluxRepository()
        puntos = _historia(28, {19: 3.0})
        # Un solo día con las 19:00 disparadas, en mitad de la ventana de prueba.
        raro = AHORA - timedelta(days=4)
        for i, punto in enumerate(puntos):
            mismo_dia = punto.time.date() == raro.date()
            if mismo_dia and (punto.time - timedelta(hours=5)).hour == 19:
                puntos[i] = EnergyPoint(time=punto.time, value=30.0)
        repo.energy_series_points_by_counter = {Variable.POWER_ACTIVE_TOTAL_POS: puntos}

        result = await power_forecast(repo, _settings(), None, 24, AHORA)

        assert result.backtest is not None
        assert result.backtest.mae_kwh < result.backtest.naive_mae_kwh


class TestElEndpoint:
    def test_devuelve_el_pronostico(
        self,
        client: TestClient,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        fake_influx_repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _historia(28, {19: 3.0}),
        }

        response = client.get(
            "/api/v1/forecast/power", params={"horizon_hours": 24}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["target"] == "import_kwh"
        assert data["horizon_hours"] == 24

    def test_horizonte_fuera_de_rango_rechazado(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(
            "/api/v1/forecast/power", params={"horizon_hours": 500}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_requiere_autenticacion(self, client: TestClient) -> None:
        assert client.get("/api/v1/forecast/power").status_code == 401
