"""F2.4 — el historial de anomalías y los cambios de nivel.

La alerta en vivo solo habla de ayer y se pierde al reiniciar. Acá el rango
entero se RECALCULA sobre los datos guardados, así que la respuesta es la
misma hoy que dentro de un mes.

Dos cosas distintas: el día atípico (fuera de su banda) y el consumo que sube
y se QUEDA arriba, que las bandas puntuales no ven porque cada día por
separado sigue cayendo dentro de lo normal.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.cache import clear_all_caches
from app.core.config import Settings
from app.models.variables import Variable
from app.schemas.influx import EnergyPoint
from app.services.alerts.history import (
    MIN_DAYS_FOR_SHIFT,
    alerts_history,
    detect_level_shift,
)
from tests.fakes import FakeInfluxRepository

TZ = "America/Bogota"
# Medianoche de Bogotá.
PRIMER_DIA = datetime(2026, 7, 1, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _sin_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    clear_all_caches()


def _settings() -> Settings:
    return Settings(_env_file=None, TIMEZONE=TZ)  # pyright: ignore[reportCallIssue]


def _dias(valores: list[float], desde: datetime = PRIMER_DIA) -> list[EnergyPoint]:
    return [
        EnergyPoint(time=desde + timedelta(days=i), value=valor) for i, valor in enumerate(valores)
    ]


class TestElCambioDeNivel:
    def test_detecta_un_salto_sostenido(self) -> None:
        """Un equipo nuevo, una nevera que se degrada: el promedio se corre y
        no vuelve."""
        puntos = _dias(
            [10.0, 10.5, 9.8, 10.2, 10.1, 9.9, 10.3, 18.0, 18.5, 17.9, 18.2, 18.1, 17.8, 18.4]
        )

        detectado = detect_level_shift(puntos)

        assert detectado is not None
        corte, antes, despues = detectado
        assert corte == 7
        assert round(antes, 1) == 10.1
        assert round(despues, 1) == 18.1

    def test_un_dia_atipico_no_es_un_cambio_de_nivel(self) -> None:
        """Una fiesta el sábado no cambió el nivel de nada."""
        valores = [10.0] * 20
        valores[10] = 40.0

        assert detect_level_shift(_dias(valores)) is None

    def test_con_pocos_dias_no_se_arriesga(self) -> None:
        assert detect_level_shift(_dias([10.0] * (MIN_DAYS_FOR_SHIFT - 1))) is None

    def test_una_serie_constante_no_tiene_de_donde_medir(self) -> None:
        """Sin dispersión no hay escala; dividir por cero daría un cambio en
        cualquier parte."""
        assert detect_level_shift(_dias([10.0] * 30)) is None

    async def test_el_mensaje_dice_desde_cuando_y_cuanto(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias([10.0] * 10 + [20.0] * 10),
        }

        result = await alerts_history(
            repo, _settings(), PRIMER_DIA, PRIMER_DIA + timedelta(days=20), None
        )

        assert result.level_shift is not None
        assert result.level_shift.direction == "up"
        assert result.level_shift.delta_pct == 100.0
        assert "11 de julio" in result.level_shift.message
        assert "se mantuvo" in result.level_shift.message


class TestLasAnomaliasDelRango:
    async def test_marca_los_dias_fuera_de_su_banda(self) -> None:
        repo = FakeInfluxRepository()
        # Cuatro semanas de lunes a domingo a 10 kWh, con un martes de 40.
        valores = [10.0] * 28
        valores[8] = 40.0  # el segundo martes del rango
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias(valores),
        }
        # La banda sale de `weekday_total_baseline`, que consulta la misma
        # serie diaria del fake.
        result = await alerts_history(
            repo, _settings(), PRIMER_DIA, PRIMER_DIA + timedelta(days=28), None
        )

        assert result.days_analyzed == 28
        assert any(a.value == 40.0 for a in result.anomalies)
        assert all(a.kind == "daily_total" for a in result.anomalies)

    async def test_las_mas_recientes_primero(self) -> None:
        repo = FakeInfluxRepository()
        valores = [10.0] * 28
        valores[8] = 40.0
        valores[22] = 45.0
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias(valores),
        }

        result = await alerts_history(
            repo, _settings(), PRIMER_DIA, PRIMER_DIA + timedelta(days=28), None
        )

        marcas = [a.timestamp for a in result.anomalies]
        assert marcas == sorted(marcas, reverse=True)

    async def test_un_rango_sin_dias_completos_no_analiza_nada(self) -> None:
        """El día en curso siempre parecería bajo frente a un día entero — la
        misma razón por la que la alerta en vivo evalúa ayer y no hoy."""
        repo = FakeInfluxRepository()
        ahora = datetime.now(tz=UTC)

        result = await alerts_history(repo, _settings(), ahora, ahora + timedelta(hours=6), None)

        assert result.days_analyzed == 0
        assert result.anomalies == []
        assert result.level_shift is None


class TestElEndpoint:
    def test_devuelve_el_historial(
        self,
        client: TestClient,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        fake_influx_repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _dias([10.0] * 10 + [20.0] * 10),
        }

        response = client.get(
            "/api/v1/alerts/history",
            params={
                "from": PRIMER_DIA.isoformat(),
                "to": (PRIMER_DIA + timedelta(days=20)).isoformat(),
            },
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["days_analyzed"] == 20
        assert data["level_shift"]["direction"] == "up"

    def test_rango_invertido_rechazado(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(
            "/api/v1/alerts/history",
            params={"from": "2026-08-10T00:00:00Z", "to": "2026-08-01T00:00:00Z"},
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_requiere_autenticacion(self, client: TestClient) -> None:
        assert client.get("/api/v1/alerts/history").status_code == 401
