"""F2.3 — los tipos de día de la instalación.

Se agrupan por la FORMA del consumo horario, no por su magnitud: dos martes
con el mismo horario y distinto calor son el mismo tipo de día.

Lo que hay que proteger: que no invente grupos donde no los hay, que el
resultado no cambie entre dos consultas iguales, y que un día con medio día
de datos no entre a definir una forma que no tuvo.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.cache import clear_all_caches
from app.models.variables import Variable
from app.schemas.influx import EnergyPoint
from app.services.analytics.archetypes import MIN_DAYS, day_archetypes
from tests.fakes import FakeInfluxRepository

TZ = "America/Bogota"
# Medianoche local del primer día (Bogotá es UTC-5).
PRIMER_DIA = datetime(2026, 5, 4, 5, tzinfo=UTC)  # lunes

# Un día de oficina: casi todo entre las 8 y las 18.
LABORAL = [0.1] * 8 + [1.5] * 10 + [0.2] * 6
# Un día de casa: consumo repartido, con pico en la noche.
DESCANSO = [0.3] * 8 + [0.4] * 10 + [1.4] * 6


@pytest.fixture(autouse=True)
def _sin_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    clear_all_caches()


def _serie(perfiles: list[list[float]], desde: datetime = PRIMER_DIA) -> list[EnergyPoint]:
    """Una serie horaria: un perfil de 24 valores por día."""
    puntos: list[EnergyPoint] = []
    for dia, perfil in enumerate(perfiles):
        base = desde + timedelta(days=dia)
        puntos.extend(
            EnergyPoint(time=base + timedelta(hours=hora), value=valor)
            for hora, valor in enumerate(perfil)
        )
    return puntos


def _semanas(cantidad: int) -> list[list[float]]:
    """Semanas de cinco días de oficina y dos de descanso."""
    return [LABORAL if dia % 7 < 5 else DESCANSO for dia in range(cantidad * 7)]


class TestCuandoHayTiposDeDia:
    async def test_separa_laborales_de_fines_de_semana(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _serie(_semanas(6)),
        }

        result = await day_archetypes(repo, None, TZ, days=42)

        assert len(result.archetypes) == 2
        etiquetas = {a.label for a in result.archetypes}
        assert etiquetas == {"Laboral", "Fin de semana"}
        assert result.silhouette is not None
        assert result.silhouette > 0.25

    async def test_la_forma_manda_sobre_la_magnitud(self) -> None:
        """Un laboral de invierno y uno de verano consumen distinto pero tienen
        la misma forma: son el mismo tipo de día."""
        repo = FakeInfluxRepository()
        perfiles: list[list[float]] = []
        for dia in range(42):
            base = LABORAL if dia % 7 < 5 else DESCANSO
            escala = 1.0 if dia % 2 == 0 else 2.5
            perfiles.append([v * escala for v in base])
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _serie(perfiles),
        }

        result = await day_archetypes(repo, None, TZ, days=42)

        assert len(result.archetypes) == 2

    async def test_cada_dia_queda_asignado_a_un_grupo(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _serie(_semanas(6)),
        }

        result = await day_archetypes(repo, None, TZ, days=42)

        assert len(result.assignments) == result.days_analyzed == 42
        assert {a.archetype for a in result.assignments} == {0, 1}

    async def test_dos_consultas_iguales_dan_los_mismos_grupos(self) -> None:
        """Con semillas al azar, el cliente vería su "tipo de día" cambiar al
        recargar sin que cambiara nada."""
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _serie(_semanas(6)),
        }

        primera = await day_archetypes(repo, None, TZ, days=42)
        clear_all_caches()
        segunda = await day_archetypes(repo, None, TZ, days=42)

        assert [a.label for a in primera.archetypes] == [a.label for a in segunda.archetypes]
        assert [a.archetype for a in primera.assignments] == [
            a.archetype for a in segunda.assignments
        ]


class TestCuandoNoLosHay:
    async def test_una_instalacion_pareja_no_se_parte_en_grupos(self) -> None:
        """Todos los días iguales: separarlos sería dibujar una frontera donde
        no hay ninguna."""
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _serie([LABORAL] * 42),
        }

        result = await day_archetypes(repo, None, TZ, days=42)

        assert result.archetypes == []
        assert result.days_analyzed == 42

    async def test_con_pocos_dias_no_se_agrupa(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _serie(_semanas(2)[: MIN_DAYS - 1]),
        }

        result = await day_archetypes(repo, None, TZ, days=20)

        assert result.archetypes == []
        assert result.silhouette is None

    async def test_un_dia_a_medias_no_define_una_forma(self) -> None:
        """Un día con seis horas de datos tiene la forma de las horas que
        faltaron, no la de su consumo."""
        repo = FakeInfluxRepository()
        perfiles = _semanas(6)
        puntos = _serie(perfiles)
        # Se recorta el tercer día a seis horas.
        tercer_dia = PRIMER_DIA + timedelta(days=2)
        puntos = [
            p
            for p in puntos
            if not (tercer_dia + timedelta(hours=6) <= p.time < tercer_dia + timedelta(days=1))
        ]
        repo.energy_series_points_by_counter = {Variable.POWER_ACTIVE_TOTAL_POS: puntos}

        result = await day_archetypes(repo, None, TZ, days=42)

        assert result.days_analyzed == 41
        assert all(a.date != "2026-05-06" for a in result.assignments)

    async def test_sin_datos(self) -> None:
        repo = FakeInfluxRepository()
        repo.energy_series_points_by_counter = {Variable.POWER_ACTIVE_TOTAL_POS: []}

        result = await day_archetypes(repo, None, TZ, days=42)

        assert result.days_analyzed == 0
        assert result.archetypes == []


class TestElEndpoint:
    def test_devuelve_los_arquetipos(
        self,
        client: TestClient,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        fake_influx_repo.energy_series_points_by_counter = {
            Variable.POWER_ACTIVE_TOTAL_POS: _serie(_semanas(6)),
        }

        response = client.get(
            "/api/v1/analytics/day-archetypes", params={"days": 42}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert len(data["archetypes"]) == 2
        # La curva de cada grupo es una repartición: suma 1.
        for arquetipo in data["archetypes"]:
            assert abs(sum(arquetipo["hourly_share"]) - 1.0) < 0.01

    def test_requiere_autenticacion(self, client: TestClient) -> None:
        assert client.get("/api/v1/analytics/day-archetypes").status_code == 401
