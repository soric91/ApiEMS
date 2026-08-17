"""F0.6 — en qué modo se lee el medidor de frontera de una sede.

La sede de referencia tiene fotovoltaica inyectando y se mide en frontera, así
que el medidor solo ve el balance neto. La mayoría de las sedes por instalar
NO tienen generación: ahí todo lo que pasa por el medidor es consumo. El modo
decide cómo se calculan varios indicadores y qué muestra el panel.

Manda lo declarado en el CRM; sin declarar, se detecta por la energía
exportada. Ausente NO es `False`: un CRM que todavía no manda el campo tiene
que caer en la detección, no quedar marcado como sede sin generación.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.cache import clear_all_caches
from app.models.variables import Variable
from app.services.analytics.site_mode import declared_mode, detect_site_mode
from app.services.crm.fleet import ClientFleet, FleetDevice, walk_devices
from tests.conftest import TEST_CLIENT_ID, TEST_DEVICE_ID, FakeFleetDirectory
from tests.fakes import FakeInfluxRepository


@pytest.fixture(autouse=True)
def _sin_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    """La detección se cachea 24 h: sin limpiar, el primer test decidiría el
    modo de todos los demás."""
    clear_all_caches()


def _fleet_con(declaracion: bool | None, fleet: ClientFleet) -> ClientFleet:
    device = FleetDevice(
        id=TEST_DEVICE_ID,
        nombre="Medidor de prueba",
        modbus_id=1,
        sede_id="sede-1",
        sede="Planta Norte",
        gateway_id="gw-1",
        gateway="GW-0001",
        gateway_en_linea=True,
        tiene_generacion=declaracion,
    )
    return ClientFleet(
        client_id=TEST_CLIENT_ID,
        devices=(device,),
        variables=fleet.variables,
        puede_ver_consumo=True,
    )


class TestLoDeclaradoEnElCrm:
    def test_manda_sobre_la_deteccion(
        self,
        app: FastAPI,
        client: TestClient,
        fleet: ClientFleet,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        app.state.fleet_directory = FakeFleetDirectory(_fleet_con(False, fleet))
        # Aunque el contador de exportación tenga energía, lo declarado manda:
        # quien conoce la instalación dijo que no hay generación.
        fake_influx_repo.energy_total_by_counter = {Variable.POWER_ACTIVE_TOTAL_NEG: 500.0}

        response = client.get("/api/v1/analytics/site-mode", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["data"]["mode"] == "consumo"
        assert response.json()["data"]["source"] == "crm"
        # Y no se gastó ninguna consulta en detectarlo.
        assert fake_influx_repo.calls == []

    def test_declarada_con_generacion(
        self,
        app: FastAPI,
        client: TestClient,
        fleet: ClientFleet,
        auth_headers: dict[str, str],
    ) -> None:
        app.state.fleet_directory = FakeFleetDirectory(_fleet_con(True, fleet))

        response = client.get("/api/v1/analytics/site-mode", headers=auth_headers)

        assert response.json()["data"] == {
            "device_id": None,
            "mode": "generacion",
            "source": "crm",
        }


class TestSinDeclarar:
    def test_una_sede_que_exporta_es_de_generacion(
        self,
        app: FastAPI,
        client: TestClient,
        fleet: ClientFleet,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        app.state.fleet_directory = FakeFleetDirectory(_fleet_con(None, fleet))
        fake_influx_repo.energy_total_by_counter = {Variable.POWER_ACTIVE_TOTAL_NEG: 120.0}

        response = client.get("/api/v1/analytics/site-mode", headers=auth_headers)

        assert response.json()["data"]["mode"] == "generacion"
        assert response.json()["data"]["source"] == "detected"

    def test_una_sede_que_nunca_exporto_es_de_consumo(
        self,
        app: FastAPI,
        client: TestClient,
        fleet: ClientFleet,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        app.state.fleet_directory = FakeFleetDirectory(_fleet_con(None, fleet))
        fake_influx_repo.energy_total_by_counter = {Variable.POWER_ACTIVE_TOTAL_NEG: 0.0}

        response = client.get("/api/v1/analytics/site-mode", headers=auth_headers)

        assert response.json()["data"]["mode"] == "consumo"
        assert response.json()["data"]["source"] == "detected"

    async def test_una_exportacion_anecdotica_no_cuenta(self) -> None:
        """Ruido de medición, no un sistema inyectando."""
        repo = FakeInfluxRepository()
        repo.energy_total_by_counter = {Variable.POWER_ACTIVE_TOTAL_NEG: 0.4}

        assert await detect_site_mode(repo, TEST_DEVICE_ID) == "consumo"


class TestLaDeclaracionDeLaFlota:
    def test_sedes_que_se_contradicen_pasan_a_deteccion(self) -> None:
        """Un cliente con una planta solar y una bodega sin nada, consultado
        sin `device_id`: elegir una de las dos sería inventar."""
        assert declared_mode([True, False]) is None

    def test_sin_ninguna_declaracion_pasa_a_deteccion(self) -> None:
        assert declared_mode([None, None]) is None
        assert declared_mode([]) is None

    def test_todas_de_acuerdo_deciden(self) -> None:
        assert declared_mode([True, True, None]) == "generacion"
        assert declared_mode([False, None]) == "consumo"


class TestLoQueLlegaDelCrm:
    def test_el_campo_ausente_no_es_false(self) -> None:
        """Un CRM viejo que todavía no manda `tiene_generacion` tiene que caer
        en la detección, no quedar marcado como sede sin generación."""
        payload = {
            "items": [
                {
                    "puede_ver_consumo": True,
                    "sites": [
                        {
                            "id": "sede-1",
                            "nombre": "Planta",
                            "gateways": [
                                {
                                    "id": "gw-1",
                                    "numero_serie": "GW-0001",
                                    "estado": "online",
                                    "equipment": [{"id": "eq-1", "nombre_dispositivo": "M1"}],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        devices, _, _ = walk_devices(payload)

        assert devices[0].tiene_generacion is None

    def test_el_campo_declarado_viaja(self) -> None:
        payload = {
            "items": [
                {
                    "puede_ver_consumo": True,
                    "sites": [
                        {
                            "id": "sede-1",
                            "nombre": "Planta",
                            "tiene_generacion": True,
                            "gateways": [
                                {
                                    "id": "gw-1",
                                    "numero_serie": "GW-0001",
                                    "estado": "online",
                                    "equipment": [{"id": "eq-1", "nombre_dispositivo": "M1"}],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        devices, _, _ = walk_devices(payload)

        assert devices[0].tiene_generacion is True


def test_requiere_autenticacion(client: TestClient) -> None:
    assert client.get("/api/v1/analytics/site-mode").status_code == 401
