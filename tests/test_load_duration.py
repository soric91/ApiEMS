"""F1.4 — la curva de duración de carga.

"El 5% del tiempo estás por encima de 4,2 kW, y ese 5% explica el 22% de tu
energía". Es lo que decide si conviene atacar los picos o el consumo de fondo.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.cache import clear_all_caches
from app.schemas.influx import TimeSeriesPoint
from app.services.analytics.load_duration import load_duration
from tests.fakes import FakeInfluxRepository

START = datetime(2026, 8, 10, tzinfo=UTC)
STOP = datetime(2026, 8, 11, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _sin_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    clear_all_caches()


def _muestras(valores: list[float]) -> list[TimeSeriesPoint]:
    return [
        TimeSeriesPoint(time=START + timedelta(minutes=15 * i), value=valor)
        for i, valor in enumerate(valores)
    ]


class TestLaCurva:
    async def test_va_de_mayor_a_menor(self) -> None:
        repo = FakeInfluxRepository()
        repo.instant_series_points = _muestras([100.0, 4000.0, 250.0, 900.0])

        result = await load_duration(repo, START, STOP, None, salida=4)

        potencias = [p.power_w for p in result.points]
        assert potencias == sorted(potencias, reverse=True)
        assert potencias[0] == 4000.0
        assert result.points[0].time_fraction == 0.0
        assert result.points[-1].time_fraction == 1.0

    async def test_solo_cuenta_la_importacion(self) -> None:
        """Durante la exportación no hay demanda que la red esté sirviendo; un
        negativo dentro de una curva de carga la vuelve ilegible."""
        repo = FakeInfluxRepository()
        repo.instant_series_points = _muestras([-3000.0, 500.0, -200.0, 800.0])

        result = await load_duration(repo, START, STOP, None, salida=10)

        assert result.sample_count == 2
        assert all(p.power_w > 0 for p in result.points)

    async def test_devuelve_como_mucho_los_puntos_pedidos(self) -> None:
        """Mandar las miles de muestras sería mandar la serie entera reordenada:
        el dibujo no cambia y la respuesta pesa."""
        repo = FakeInfluxRepository()
        repo.instant_series_points = _muestras([float(v) for v in range(1, 1001)])

        result = await load_duration(repo, START, STOP, None, salida=50)

        assert len(result.points) == 50
        assert result.sample_count == 1000

    async def test_rango_sin_datos(self) -> None:
        repo = FakeInfluxRepository()
        repo.instant_series_points = []

        result = await load_duration(repo, START, STOP, None)

        assert result.points == []
        assert result.p50_w is None
        assert result.top_energy_share is None


class TestLosPercentiles:
    async def test_percentiles_de_la_curva(self) -> None:
        repo = FakeInfluxRepository()
        # 100 muestras de 1 a 100 W.
        repo.instant_series_points = _muestras([float(v) for v in range(1, 101)])

        result = await load_duration(repo, START, STOP, None)

        # p5 = la potencia superada el 5% del tiempo: la muestra número 5
        # empezando por la más alta.
        assert result.p5_w == 95.0
        assert result.p50_w == 50.0
        assert result.p95_w == 5.0

    async def test_un_consumo_de_picos_concentra_la_energia(self) -> None:
        repo = FakeInfluxRepository()
        # 95 muestras de 100 W y 5 de 10 kW: el 5% del tiempo se lleva más de
        # la mitad de la energía.
        repo.instant_series_points = _muestras([100.0] * 95 + [10_000.0] * 5)

        result = await load_duration(repo, START, STOP, None)

        assert result.top_fraction == 0.05
        assert result.top_energy_share is not None
        assert result.top_energy_share > 0.8

    async def test_un_consumo_parejo_no_concentra(self) -> None:
        repo = FakeInfluxRepository()
        repo.instant_series_points = _muestras([500.0] * 100)

        result = await load_duration(repo, START, STOP, None)

        assert result.top_energy_share is not None
        # Con todo igual, el 5% del tiempo aporta el 5% de la energía.
        assert result.top_energy_share == 0.05


class TestElEndpoint:
    def test_devuelve_la_curva(
        self,
        client: TestClient,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        fake_influx_repo.instant_series_points = _muestras([100.0, 900.0, 4000.0])

        response = client.get(
            "/api/v1/analytics/load-duration", params={"points": 3}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert len(data["points"]) == 3
        assert data["points"][0]["power_w"] == 4000.0
        assert data["sample_seconds"] == 900

    def test_un_solo_punto_no_es_una_curva(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(
            "/api/v1/analytics/load-duration", params={"points": 1}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_requiere_autenticacion(self, client: TestClient) -> None:
        assert client.get("/api/v1/analytics/load-duration").status_code == 401
