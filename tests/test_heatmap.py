"""F1.1 — el mapa de calor hora x día.

La misma energía de siempre reordenada en una cuadrícula. Lo que hay que
proteger acá es la hora LOCAL (agrupar en UTC corre el consumo nocturno cinco
casillas en Bogotá) y que una hora sin dato quede en `null`, no en cero.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.models.variables import Variable
from app.schemas.influx import EnergyPoint
from app.schemas.tariff import TariffConfig, TariffPeriod
from app.services.analytics.heatmap import heatmap
from tests.fakes import FakeInfluxRepository

START = datetime(2026, 8, 10, tzinfo=UTC)
STOP = datetime(2026, 8, 12, tzinfo=UTC)
SIN_TARIFA = TariffConfig()


def _energia(momentos: list[tuple[datetime, float]]) -> list[EnergyPoint]:
    return [EnergyPoint(time=time, value=value) for time, value in momentos]


class TestLaCuadricula:
    async def test_agrupa_por_hora_local_no_utc(self) -> None:
        """2026-08-11T02:00Z es el 10 de agosto a las 21:00 en Bogotá. Agrupado
        en UTC caería en el día siguiente a las 2 a.m., corriendo el consumo de
        la noche cinco casillas."""
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _energia(
                [(datetime(2026, 8, 11, 2, tzinfo=UTC), 1.5)]
            ),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }

        result = await heatmap(
            repo, START, STOP, None, "import", SIN_TARIFA, "America/Bogota"
        )

        assert result.dates == ["2026-08-10"]
        assert result.values[0][21] == 1.5
        assert result.values[0][2] is None

    async def test_una_hora_sin_dato_queda_en_null(self) -> None:
        """Pintar de "consumo cero" las horas que el gateway estuvo caído es el
        error que /analytics/coverage existe para evitar."""
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _energia(
                [(datetime(2026, 8, 10, 10, tzinfo=UTC), 2.0)]
            ),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }

        result = await heatmap(
            repo, START, STOP, None, "import", SIN_TARIFA, "America/Bogota"
        )

        fila = result.values[0]
        assert fila[5] == 2.0  # 10:00 UTC = 05:00 Bogotá
        assert sum(1 for celda in fila if celda is None) == 23

    async def test_rango_sin_datos_devuelve_cuadricula_vacia(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: [],
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }

        result = await heatmap(
            repo, START, STOP, None, "import", SIN_TARIFA, "America/Bogota"
        )

        assert result.dates == []
        assert result.values == []


class TestLasMetricas:
    async def test_net_resta_lo_exportado(self) -> None:
        repo = FakeInfluxRepository()
        momento = datetime(2026, 8, 10, 15, tzinfo=UTC)
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _energia([(momento, 1.0)]),
            Variable.POWER_ACTIVE_TOTAL_NEG: _energia([(momento, 4.0)]),
        }

        result = await heatmap(repo, START, STOP, None, "net", SIN_TARIFA, "America/Bogota")

        assert result.unit == "kWh"
        assert result.values[0][10] == -3.0  # exportador neto a las 10:00 local

    async def test_cost_usa_la_tarifa_del_mes_de_la_casilla(self) -> None:
        """El mapa puede cruzar un cambio de tarifa: cada hora cuesta lo que
        costaba ESE mes, no lo que cuesta hoy."""
        tarifa = TariffConfig(
            periods=[
                TariffPeriod(month="2026-07", cu_cop_kwh=800.0, excedente_cop_kwh=100.0),
                TariffPeriod(month="2026-08", cu_cop_kwh=900.0, excedente_cop_kwh=110.0),
            ]
        )
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _energia(
                [
                    (datetime(2026, 7, 20, 15, tzinfo=UTC), 2.0),
                    (datetime(2026, 8, 10, 15, tzinfo=UTC), 2.0),
                ]
            ),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }

        result = await heatmap(
            repo,
            datetime(2026, 7, 1, tzinfo=UTC),
            STOP,
            None,
            "cost",
            tarifa,
            "America/Bogota",
        )

        assert result.unit == "COP"
        julio = result.dates.index("2026-07-20")
        agosto = result.dates.index("2026-08-10")
        assert result.values[julio][10] == 1600.0
        assert result.values[agosto][10] == 1800.0

    async def test_un_mes_sin_tarifa_no_inventa_precio(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _energia(
                [(datetime(2026, 8, 10, 15, tzinfo=UTC), 2.0)]
            ),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }

        result = await heatmap(repo, START, STOP, None, "cost", SIN_TARIFA, "America/Bogota")

        assert result.values[0][10] == 0.0


class TestElEndpoint:
    def test_devuelve_la_cuadricula(
        self,
        client: TestClient,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        fake_influx_repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _energia(
                [(datetime(2026, 8, 10, 15, tzinfo=UTC), 3.0)]
            ),
            Variable.POWER_ACTIVE_TOTAL_NEG: [],
        }

        response = client.get(
            "/api/v1/analytics/heatmap",
            params={
                "from": "2026-08-10T00:00:00Z",
                "to": "2026-08-12T00:00:00Z",
                "metric": "import",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["metric"] == "import"
        assert data["unit"] == "kWh"
        assert data["values"][0][10] == 3.0

    def test_metrica_invalida_rechazada(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(
            "/api/v1/analytics/heatmap", params={"metric": "inventada"}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_requiere_autenticacion(self, client: TestClient) -> None:
        assert client.get("/api/v1/analytics/heatmap").status_code == 401


def test_el_rango_por_defecto_es_hoy(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    """Igual que el resto de /analytics: sin from/to, el día en curso."""
    fake_influx_repo.energy_series_points_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [],
        Variable.POWER_ACTIVE_TOTAL_NEG: [],
    }

    response = client.get("/api/v1/analytics/heatmap", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    inicio = datetime.fromisoformat(data["period_start"])
    fin = datetime.fromisoformat(data["period_end"])
    assert fin - inicio <= timedelta(days=1)
