"""F3.2 — la comparación entre sedes del mismo cliente.

Sin cruzar clientes: comparar contra las sedes de otra empresa exigiría datos
que este token no autoriza a ver, ni siquiera para promediarlos.

Lo que hay que proteger: que no mezcle sedes con y sin generación (la solar
importa estructuralmente menos y haría ver a las demás como derrochadoras), y
que no publique una posición cuando el grupo es demasiado chico para que
signifique algo.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.cache import clear_all_caches
from app.models.variables import Variable
from app.services.analytics.benchmark import benchmark
from app.services.crm.fleet import ClientFleet, FleetDevice
from tests.conftest import TEST_CLIENT_ID, TEST_DEVICE_ID, FakeFleetDirectory
from tests.fakes import FakeInfluxRepository

TZ = "America/Bogota"


@pytest.fixture(autouse=True)
def _sin_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    clear_all_caches()


def _device(id_: str, nombre: str, *, generacion: bool | None = False) -> FleetDevice:
    return FleetDevice(
        id=id_,
        nombre=nombre,
        modbus_id=1,
        sede_id=f"sede-{id_}",
        sede=f"Sede {nombre}",
        gateway_id="gw-1",
        gateway="GW-0001",
        gateway_en_linea=True,
        tiene_generacion=generacion,
    )


class _RepoPorEquipo(FakeInfluxRepository):
    """Un consumo mensual distinto por equipo."""

    def __init__(self, consumos: dict[str, float]) -> None:
        super().__init__()
        self.consumos = consumos

    async def energy_total(
        self,
        counter: Variable,
        start: object = None,
        stop: object = None,
        device_id: str | None = None,
        devices: object | None = None,
    ) -> float:
        return self.consumos.get(device_id or "", 0.0)


class TestElRanking:
    async def test_ubica_la_sede_entre_las_demas(self) -> None:
        devices = (
            _device("a", "Planta"),
            _device("b", "Bodega"),
            _device("c", "Oficina"),
        )
        # 30 días: 600 kWh = 20/día, 300 = 10/día, 150 = 5/día.
        repo = _RepoPorEquipo({"a": 600.0, "b": 300.0, "c": 150.0})

        result = await benchmark(repo, devices, "b", TZ, days=30)

        assert result.own_kwh_per_day == 10.0
        assert result.median_kwh_per_day == 10.0
        # Una de las tres consume menos que ella: 50% del grupo.
        assert result.percentile == 50.0
        assert result.enough_peers is True
        # De menos a más consumo, para leerse como ranking de eficiencia.
        assert [p.name for p in result.peers] == ["Oficina", "Bodega", "Planta"]
        assert [p.is_self for p in result.peers] == [False, True, False]

    async def test_una_sede_sin_consumo_no_entra_al_grupo(self) -> None:
        """Un cero no es un ahorro: es una sede que no está midiendo, y bajaría
        la mediana del grupo."""
        devices = (
            _device("a", "Planta"),
            _device("b", "Bodega"),
            _device("c", "Oficina"),
            _device("d", "Apagada"),
        )
        repo = _RepoPorEquipo({"a": 600.0, "b": 300.0, "c": 150.0, "d": 0.0})

        result = await benchmark(repo, devices, "b", TZ, days=30)

        assert [p.name for p in result.peers] == ["Oficina", "Bodega", "Planta"]


class TestLosGrupos:
    async def test_no_mezcla_sedes_con_y_sin_generacion(self) -> None:
        devices = (
            _device("a", "Con solar", generacion=True),
            _device("b", "Sin solar", generacion=False),
            _device("c", "Sin solar 2", generacion=False),
            _device("d", "Sin solar 3", generacion=False),
        )
        repo = _RepoPorEquipo({"a": 30.0, "b": 300.0, "c": 320.0, "d": 280.0})

        result = await benchmark(repo, devices, "b", TZ, days=30)

        nombres = {p.name for p in result.peers}
        assert "Con solar" not in nombres
        assert len(result.peers) == 3

    async def test_las_no_declaradas_son_su_propio_grupo(self) -> None:
        """Mezclar lo declarado con lo desconocido sería suponer lo que no se
        sabe."""
        devices = (
            _device("a", "Sin declarar", generacion=None),
            _device("b", "Declarada", generacion=False),
        )
        repo = _RepoPorEquipo({"a": 300.0, "b": 320.0})

        result = await benchmark(repo, devices, "a", TZ, days=30)

        assert [p.name for p in result.peers] == ["Sin declarar"]


class TestCuandoElGrupoEsChico:
    async def test_con_dos_sedes_no_publica_posicion(self) -> None:
        """"Estás por encima de la mediana" con dos sedes solo dice "consumes
        más que la otra"."""
        devices = (_device("a", "Planta"), _device("b", "Bodega"))
        repo = _RepoPorEquipo({"a": 600.0, "b": 300.0})

        result = await benchmark(repo, devices, "b", TZ, days=30)

        assert result.enough_peers is False
        assert result.percentile is None
        assert result.median_kwh_per_day is None
        # El dato propio sí se informa: eso es una medición, no una comparación.
        assert result.own_kwh_per_day == 10.0

    async def test_una_sede_ajena_a_la_flota_no_devuelve_nada(self) -> None:
        repo = _RepoPorEquipo({"a": 600.0})

        result = await benchmark(repo, (_device("a", "Planta"),), "otro", TZ, days=30)

        assert result.own_kwh_per_day is None
        assert result.peers == []


class TestElEndpoint:
    def test_devuelve_la_comparacion(
        self,
        app: FastAPI,
        client: TestClient,
        fleet: ClientFleet,
        auth_headers: dict[str, str],
    ) -> None:
        devices = (
            _device(TEST_DEVICE_ID, "Planta"),
            _device("b", "Bodega"),
            _device("c", "Oficina"),
        )
        app.state.fleet_directory = FakeFleetDirectory(
            ClientFleet(
                client_id=TEST_CLIENT_ID,
                devices=devices,
                variables=fleet.variables,
                puede_ver_consumo=True,
            )
        )

        response = client.get(
            "/api/v1/analytics/benchmark",
            params={"device_id": TEST_DEVICE_ID},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["device_id"] == TEST_DEVICE_ID
        assert len(data["peers"]) == 3

    def test_una_sede_de_otro_cliente_no_se_puede_consultar(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """El recorte por flota es lo que impide comparar contra otra empresa
        por accidente: un equipo ajeno simplemente no existe para este token."""
        response = client.get(
            "/api/v1/analytics/benchmark",
            params={"device_id": "equipo-de-otra-empresa"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["data"]["peers"] == []

    def test_requiere_autenticacion(self, client: TestClient) -> None:
        response = client.get("/api/v1/analytics/benchmark", params={"device_id": "x"})
        assert response.status_code == 401
