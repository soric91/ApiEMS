"""`GET /api/v1/variables` — qué se puede graficar.

El endpoint existe para que el panel no tenga una lista fija de variables. Lo
que se prueba acá es sobre todo lo que **no** devuelve: la fase que el medidor
no reporta, y las variables de otra empresa.
"""

from collections.abc import Sequence
from datetime import timedelta
from typing import Any, cast

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.repositories.influx import InfluxRepository
from app.repositories.scoped import ScopedInfluxRepository
from tests.conftest import TEST_DEVICE_ID

RUTA = "/api/v1/variables"


def _nombres(payload: dict[str, Any]) -> list[str]:
    return [item["nombre"] for item in payload["data"]]


def _graficables(payload: dict[str, Any]) -> list[str]:
    """Lo que el panel llega a dibujar: solo las que reportaron algo."""
    return [item["nombre"] for item in payload["data"] if item["con_datos"]]


class _InteriorEspia:
    """Repositorio sin recortar que solo anota con qué equipos lo llamaron."""

    def __init__(self) -> None:
        self.devices: tuple[str, ...] = ()

    async def field_keys(
        self, devices: Sequence[str], lookback: timedelta = timedelta(days=30)
    ) -> list[str]:
        self.devices = tuple(devices)
        return []


class TestOnlyWhatHasData:
    def test_a_variable_without_readings_is_not_graphable(
        self, client: TestClient, fake_influx_repo: Any, auth_headers: dict[str, str]
    ) -> None:
        """El caso que motivó todo: la flota tiene tres variables cargadas y
        el medidor solo reportó dos. La tercera se informa pero no se grafica —
        antes el panel le dibujaba una gráfica vacía."""
        fake_influx_repo.field_keys_result = ["PhV_phsA", "A_phsA"]

        response = client.get(RUTA, headers=auth_headers)

        assert response.status_code == 200
        assert _graficables(response.json()) == ["A_phsA", "PhV_phsA"]

    def test_a_field_in_influx_that_is_not_in_the_fleet_is_ignored(
        self, client: TestClient, fake_influx_repo: Any, auth_headers: dict[str, str]
    ) -> None:
        """Influx conserva series viejas con nombres que ya nadie tiene dado
        de alta. Sin el cruce contra la flota, resucitarían en el panel."""
        fake_influx_repo.field_keys_result = ["PhV_phsA", "Voltaje_A", "PhV_phsC"]

        response = client.get(RUTA, headers=auth_headers)

        assert "Voltaje_A" not in _nombres(response.json())
        assert _graficables(response.json()) == ["PhV_phsA"]

    def test_no_readings_at_all_still_lists_what_the_crm_declared(
        self, client: TestClient, fake_influx_repo: Any, auth_headers: dict[str, str]
    ) -> None:
        """La distinción que el panel necesita para no acusar al CRM.

        Con InfluxDB caído o vacío no reportó ninguna, y antes eso salía como
        lista vacía — idéntico a un cliente sin nada cargado. El panel mandaba
        entonces a dar de alta variables que ya estaban dadas de alta. Ahora las
        variables siguen apareciendo, todas con `con_datos` en `false`.
        """
        fake_influx_repo.field_keys_result = []

        response = client.get(RUTA, headers=auth_headers)

        assert response.status_code == 200
        payload = response.json()
        assert len(payload["data"]) > 0
        assert _graficables(payload) == []
        assert all(item["con_datos"] is False for item in payload["data"])


class TestTheMetadataComesFromTheCrm:
    """ApiEMS no sabe qué significa `PhV_phsA`. Lo transporta."""

    def test_it_carries_label_unit_and_magnitude(
        self, client: TestClient, fake_influx_repo: Any, auth_headers: dict[str, str]
    ) -> None:
        fake_influx_repo.field_keys_result = ["PhV_phsA"]

        response = client.get(RUTA, headers=auth_headers)

        por_nombre = {v["nombre"]: v for v in response.json()["data"]}
        variable = por_nombre["PhV_phsA"]
        assert variable["etiqueta"] == "Tensión fase A"
        assert variable["unidad"] == "V"
        assert variable["magnitud"] == "tension"
        assert variable["fase"] == "A"

    def test_a_counter_is_marked_as_such(
        self, client: TestClient, fake_influx_repo: Any, auth_headers: dict[str, str]
    ) -> None:
        """Sin esta marca el panel promediaría un contador monótono y
        mostraría un consumo que no existe."""
        fake_influx_repo.field_keys_result = ["TotWh_import", "PhV_phsA"]

        response = client.get(RUTA, headers=auth_headers)

        por_nombre = {v["nombre"]: v for v in response.json()["data"]}

        assert por_nombre["TotWh_import"]["acumulativa"] is True
        assert por_nombre["PhV_phsA"]["acumulativa"] is False

    def test_it_says_which_devices_report_it(
        self, client: TestClient, fake_influx_repo: Any, auth_headers: dict[str, str]
    ) -> None:
        fake_influx_repo.field_keys_result = ["PhV_phsA"]

        response = client.get(RUTA, headers=auth_headers)

        assert response.json()["data"][0]["equipos"] == [TEST_DEVICE_ID]


class TestItIsScoped:
    async def test_the_query_only_covers_this_clients_devices(self) -> None:
        """Sin el recorte, un cliente vería qué mediciones tiene otro — que ya
        es contar algo de una empresa ajena.

        Se prueba contra `ScopedInfluxRepository` y no por HTTP a propósito:
        en los tests HTTP el doble ocupa el lugar del repositorio ya acotado,
        así que el recorte pasa por afuera y una aserción ahí no probaría nada.
        """
        interior = _InteriorEspia()
        acotado = ScopedInfluxRepository(
            cast(InfluxRepository, interior), frozenset({TEST_DEVICE_ID})
        )

        await acotado.field_keys()

        assert interior.devices == (TEST_DEVICE_ID,)

    def test_without_a_token_it_is_rejected(self, client: TestClient) -> None:
        assert client.get(RUTA).status_code == 401


class TestLaVentanaEsConfigurable:
    """Cuánto histórico se recorre para decidir qué variables tienen datos.

    `schema.fieldKeys` con predicado no lee el índice pese al nombre: recorre
    los datos. Con la ventana en 30 días fue la consulta más cara del panel y
    la que lo tumbó en producción, así que el valor tiene que poder ajustarse
    por despliegue en vez de estar escrito en el código.
    """

    def test_la_consulta_usa_el_valor_configurado(
        self,
        app: Any,
        client: TestClient,
        fake_influx_repo: Any,
        auth_headers: dict[str, str],
        monkeypatch: Any,
    ) -> None:
        monkeypatch.setenv("VARIABLES_LOOKBACK_HOURS", "3")
        get_settings.cache_clear()
        app.dependency_overrides[get_settings] = get_settings

        client.get(RUTA, headers=auth_headers)

        assert fake_influx_repo.field_keys_lookback == timedelta(hours=3)
        get_settings.cache_clear()
