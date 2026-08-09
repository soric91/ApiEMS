from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.cache import clear_all_caches
from app.core.config import get_settings
from app.core.crm_identity import CrmIdentity, InvalidIdentityError
from app.dependencies.influx import get_influx_repository
from app.dependencies.tariff import get_tariff_config
from app.main import create_app
from app.schemas.tariff import TariffConfig
from app.services.crm.fleet import ClientFleet, FleetDevice, FleetVariable
from tests.fakes import FakeInfluxRepository, FakeInfluxService

# El equipo que usan los tests de tiempo real y WebSocket. Es el mismo valor
# que llega como `identify_device` en una lectura.
TEST_DEVICE_ID = "bf6a469f-4c2a-4402-9438-49a491ad2238"
TEST_CLIENT_ID = "801a7729-7925-4d9a-bbfe-a73233149922"
# Cualquier cadena sirve: el verificador está sustituido por un doble. Lo que
# se ejercita es qué hace la aplicación con una identidad, no la criptografía
# —eso vive en los tests del CRM, que es quien firma.
TEST_TOKEN = "token-de-prueba"


@pytest.fixture(autouse=True)
def _clear_ttl_caches() -> None:  # pyright: ignore[reportUnusedFunction]
    # Las TTLCache de @cached son globales al módulo; sin esto, un objeto
    # FakeInfluxRepository de un test previo cuyo id() de memoria fue
    # reutilizado por el GC podría "acertar" una clave de cache ajena.
    clear_all_caches()


# Un monofásico: tensión y corriente de fase A, más un contador. Deliberadamente
# sin fase B ni C — es el caso que motivó todo esto, y así los tests notan si el
# panel vuelve a asumir que las tres fases siempre están.
TEST_VARIABLES: tuple[FleetVariable, ...] = (
    FleetVariable(
        nombre="PhV_phsA",
        etiqueta="Tensión fase A",
        unidad="V",
        magnitud="tension",
        fase="A",
        acumulativa=False,
        equipos=frozenset({TEST_DEVICE_ID}),
    ),
    FleetVariable(
        nombre="A_phsA",
        etiqueta="Corriente fase A",
        unidad="A",
        magnitud="corriente",
        fase="A",
        acumulativa=False,
        equipos=frozenset({TEST_DEVICE_ID}),
    ),
    FleetVariable(
        nombre="TotWh_import",
        etiqueta="Energía activa importada",
        unidad="kWh",
        magnitud="energia_importada",
        fase="total",
        acumulativa=True,
        equipos=frozenset({TEST_DEVICE_ID}),
    ),
)


@pytest.fixture
def fleet() -> ClientFleet:
    """La flota que ve el cliente de los tests."""
    return ClientFleet(
        client_id=TEST_CLIENT_ID,
        devices=(
            FleetDevice(
                id=TEST_DEVICE_ID,
                nombre="Medidor de prueba",
                modbus_id=1,
                sede_id="sede-1",
                sede="Planta Norte",
                gateway_id="gw-1",
                gateway="GW-0001",
                gateway_en_linea=True,
            ),
        ),
        # Ordenadas por nombre, igual que `FleetDirectory.for_client`: si el
        # fixture usara otro orden, un cambio de orden en producción pasaría
        # inadvertido acá.
        variables=tuple(sorted(TEST_VARIABLES, key=lambda v: v.nombre)),
        puede_ver_consumo=True,
    )


class FakeIdentityVerifier:
    """Acepta un token conocido y rechaza cualquier otro.

    Sustituye a la verificación contra el JWKS del CRM: un test de ApiEMS no
    debería depender de que el CRM esté levantado, y la firma ya se prueba del
    lado que firma.
    """

    def __init__(
        self, client_id: str = TEST_CLIENT_ID, *, impersonated: bool = False
    ) -> None:
        self.client_id = client_id
        self.impersonated = impersonated

    def verify(self, token: str) -> CrmIdentity:
        if token != TEST_TOKEN:
            raise InvalidIdentityError("Token inválido o vencido")
        return CrmIdentity(
            user_id="admin-de-prueba" if self.impersonated else "user-de-prueba",
            role="admin" if self.impersonated else "cliente",
            client_id=self.client_id,
            scope="full",
            impersonated=self.impersonated,
        )


class FakeFleetDirectory:
    def __init__(self, fleet: ClientFleet) -> None:
        self._fleet = fleet

    async def for_client(self, client_id: str) -> ClientFleet:
        return self._fleet


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("CRM_BASE_URL", "http://crm.de-prueba")
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def fake_influx_repo() -> FakeInfluxRepository:
    return FakeInfluxRepository()


@pytest.fixture
def tariff_config() -> TariffConfig:
    # Vacía por defecto — la fuente real es CRMBackend (RemoteTariffStore),
    # que un test HTTP no debe golpear. Un test que necesite una tarifa
    # concreta reasigna app.dependency_overrides[get_tariff_config] él mismo.
    return TariffConfig()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Lo que manda un cliente ya autenticado contra el CRM."""
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def client(
    app: FastAPI,
    fake_influx_repo: FakeInfluxRepository,
    tariff_config: TariffConfig,
    fleet: ClientFleet,
) -> Iterator[TestClient]:
    # Los endpoints reales de InfluxDB/CRMBackend se sustituyen por dobles en
    # memoria: el cliente real solo se ejercita en los smoke tests manuales.
    app.dependency_overrides[get_influx_repository] = lambda: fake_influx_repo
    app.dependency_overrides[get_tariff_config] = lambda: tariff_config
    with TestClient(app) as test_client:
        app.state.influx = FakeInfluxService()  # evita ping() real tras el lifespan
        # Identidad y flota se sustituyen en app.state, no con
        # dependency_overrides: así la cadena real de autenticación sí corre
        # —una petición sin token tiene que seguir dando 401— y lo único
        # falseado es de dónde salen la clave pública y el árbol de la flota.
        # El WebSocket además los lee de acá directamente.
        app.state.identity_verifier = FakeIdentityVerifier()
        app.state.fleet_directory = FakeFleetDirectory(fleet)
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def fleet_de_dos(app: FastAPI, fleet: ClientFleet) -> None:
    """Una flota con dos equipos, para probar que no se mezclan.

    Con uno solo, filtrar por equipo y no filtrar dan el mismo resultado: el
    test pasaría igual con el filtro roto. Hace falta un segundo equipo visible
    para que la diferencia exista.
    """
    segundo = FleetDevice(
        id="11111111-2222-4333-8444-555555555555",
        nombre="Segundo medidor",
        modbus_id=2,
        sede_id="sede-1",
        sede="Planta Norte",
        gateway_id="gw-1",
        gateway="GW-0001",
        gateway_en_linea=True,
    )
    app.state.fleet_directory = FakeFleetDirectory(
        ClientFleet(
            client_id=fleet.client_id,
            devices=(*fleet.devices, segundo),
            variables=fleet.variables,
            puede_ver_consumo=True,
        )
    )
