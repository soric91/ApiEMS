"""F1.5 — cuánto dato hay realmente en un rango.

Un hueco no es consumo cero, pero se ve igual: un gateway caído diez horas
deja un día que parece de bajo consumo. Es el prerequisito de cualquier
comparación entre periodos.
"""

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.influx import TimeSeriesPoint
from app.services.analytics.coverage import coverage
from app.services.crm.fleet import ClientFleet, FleetDevice, walk_devices
from tests.conftest import TEST_CLIENT_ID, TEST_DEVICE_ID, FakeFleetDirectory
from tests.fakes import FakeInfluxRepository

START = datetime(2026, 8, 10, tzinfo=UTC)
HORA = timedelta(hours=1)


def _counts(values: list[float]) -> list[TimeSeriesPoint]:
    return [TimeSeriesPoint(time=START + timedelta(hours=i), value=v) for i, v in enumerate(values)]


class TestConIntervaloDeclarado:
    async def test_ventana_completa_e_incompleta(self) -> None:
        repo = FakeInfluxRepository()
        # Publicando cada 60 s se esperan 60 muestras por hora. La tercera hora
        # solo trajo 6: el gateway estuvo caído casi toda la hora.
        repo.sample_counts_points = _counts([60.0, 60.0, 6.0])

        result = await coverage(repo, START, START + 3 * HORA, HORA, TEST_DEVICE_ID, 60)

        assert result.expected_per_bucket == 60.0
        assert result.expected_source == "declarado"
        assert [point.ratio for point in result.points] == [1.0, 1.0, 0.1]
        assert result.incomplete_buckets == 1
        assert result.overall_ratio == round(126 / 180, 4)

    async def test_una_ventana_sin_ninguna_lectura_cuenta_como_hueco(self) -> None:
        """`createEmpty: true` en la consulta hace que una hora sin datos
        aparezca en 0 en vez de desaparecer. Si desapareciera, la cobertura
        daría 100% sobre las horas que sí llegaron."""
        repo = FakeInfluxRepository()
        repo.sample_counts_points = _counts([60.0, 0.0])

        result = await coverage(repo, START, START + 2 * HORA, HORA, TEST_DEVICE_ID, 60)

        assert result.points[1].samples == 0
        assert result.points[1].ratio == 0.0
        assert result.overall_ratio == 0.5

    async def test_una_muestra_de_mas_no_da_mas_de_100(self) -> None:
        """El reloj del equipo redondea y una ventana puede recibir una muestra
        extra; un 104% de cobertura no significa nada."""
        repo = FakeInfluxRepository()
        repo.sample_counts_points = _counts([63.0])

        result = await coverage(repo, START, START + HORA, HORA, TEST_DEVICE_ID, 60)

        assert result.points[0].ratio == 1.0
        assert result.overall_ratio == 1.0


class TestSinIntervaloDeclarado:
    async def test_se_infiere_del_propio_rango(self) -> None:
        """Sin intervalo en el CRM, la referencia es lo que el equipo consigue
        cuando todo va bien (percentil 90), no un número inventado."""
        repo = FakeInfluxRepository()
        repo.sample_counts_points = _counts([60.0, 60.0, 60.0, 60.0, 12.0])

        result = await coverage(repo, START, START + 5 * HORA, HORA, TEST_DEVICE_ID, None)

        assert result.expected_source == "inferido"
        assert result.expected_per_bucket == 60.0
        assert result.points[4].ratio == 0.2

    async def test_sin_ninguna_muestra_no_se_inventa_una_referencia(self) -> None:
        repo = FakeInfluxRepository()
        repo.sample_counts_points = []

        result = await coverage(repo, START, START + HORA, HORA, TEST_DEVICE_ID, None)

        assert result.expected_source == "desconocido"
        assert result.expected_per_bucket is None
        assert result.overall_ratio is None
        assert result.points == []


class TestElEndpoint:
    def test_usa_el_intervalo_declarado_del_gateway(
        self,
        app: FastAPI,
        client: TestClient,
        fleet: ClientFleet,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        app.state.fleet_directory = FakeFleetDirectory(_fleet_con(60, fleet))
        fake_influx_repo.sample_counts_points = _counts([60.0, 30.0])

        response = client.get(
            "/api/v1/analytics/coverage",
            params={"bucket_seconds": 3600},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["expected_source"] == "declarado"
        assert data["expected_per_bucket"] == 60.0
        assert data["incomplete_buckets"] == 1

    def test_equipos_con_ritmos_distintos_pasan_a_inferencia(
        self,
        app: FastAPI,
        client: TestClient,
        fleet: ClientFleet,
        fake_influx_repo: FakeInfluxRepository,
        auth_headers: dict[str, str],
    ) -> None:
        """Consultado sin `device_id` sobre dos gateways que publican a ritmos
        distintos, cualquier intervalo sería la cobertura de uno y el error del
        otro."""
        primero = _fleet_con(60, fleet).devices[0]
        segundo = FleetDevice(
            id="otro-equipo",
            nombre="Medidor 2",
            modbus_id=2,
            sede_id="sede-1",
            sede="Planta Norte",
            gateway_id="gw-2",
            gateway="GW-0002",
            gateway_en_linea=True,
            intervalo_lectura_segundos=300,
        )
        app.state.fleet_directory = FakeFleetDirectory(
            ClientFleet(
                client_id=TEST_CLIENT_ID,
                devices=(primero, segundo),
                variables=fleet.variables,
                puede_ver_consumo=True,
            )
        )
        fake_influx_repo.sample_counts_points = _counts([60.0, 60.0])

        response = client.get("/api/v1/analytics/coverage", headers=auth_headers)

        assert response.json()["data"]["expected_source"] == "inferido"

    def test_requiere_autenticacion(self, client: TestClient) -> None:
        assert client.get("/api/v1/analytics/coverage").status_code == 401


def test_el_intervalo_del_gateway_llega_del_crm() -> None:
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
                                "intervalo_lectura_segundos": 30,
                                "equipment": [{"id": "eq-1", "nombre_dispositivo": "M1"}],
                            }
                        ],
                    }
                ],
            }
        ]
    }

    devices, _, _ = walk_devices(payload)

    assert devices[0].intervalo_lectura_segundos == 30


def _fleet_con(intervalo: int | None, fleet: ClientFleet) -> ClientFleet:
    device = FleetDevice(
        id=TEST_DEVICE_ID,
        nombre="Medidor de prueba",
        modbus_id=1,
        sede_id="sede-1",
        sede="Planta Norte",
        gateway_id="gw-1",
        gateway="GW-0001",
        gateway_en_linea=True,
        intervalo_lectura_segundos=intervalo,
    )
    return ClientFleet(
        client_id=TEST_CLIENT_ID,
        devices=(device,),
        variables=fleet.variables,
        puede_ver_consumo=True,
    )
