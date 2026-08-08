"""Un administrador del CRM mirando los datos de una empresa.

ApiEMS no decide quién puede suplantar — eso lo firma CRMBackend. Lo único
que decide acá es qué cambia cuando el token dice que hubo suplantación, y la
respuesta es una sola cosa: `puede_ver_consumo` deja de aplicar.
"""

from dataclasses import replace
from typing import Any

from fastapi import status
from fastapi.testclient import TestClient

from tests.conftest import TEST_TOKEN, FakeFleetDirectory, FakeIdentityVerifier
from tests.fakes import FakeInfluxService

RUTA = "/api/v1/variables"


def replace_flag(fleet: Any, value: bool) -> Any:
    return replace(fleet, puede_ver_consumo=value)


def _pedir(app: Any, fleet: Any, *, impersonated: bool) -> Any:
    with TestClient(app) as client:
        app.state.influx = FakeInfluxService()
        app.state.identity_verifier = FakeIdentityVerifier(impersonated=impersonated)
        app.state.fleet_directory = FakeFleetDirectory(fleet)
        return client.get(RUTA, headers={"Authorization": f"Bearer {TEST_TOKEN}"})


class TestTheConsumptionFlag:
    def test_a_client_is_still_blocked_when_it_is_off(
        self, app: Any, fleet: Any
    ) -> None:
        """La regla no se aflojó: sigue siendo lo que ve el cliente."""
        apagada = replace_flag(fleet, False)

        response = _pedir(app, apagada, impersonated=False)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_admin_gets_in_anyway(self, app: Any, fleet: Any) -> None:
        """El caso que motivó esto: revisar una empresa antes de habilitarla.

        Si el administrador también quedara afuera, la única forma de ver qué
        tiene cargada sería habilitársela al cliente primero — es decir,
        mostrarle datos sin haberlos revisado.
        """
        apagada = replace_flag(fleet, False)

        response = _pedir(app, apagada, impersonated=True)

        assert response.status_code == status.HTTP_200_OK

    def test_a_client_with_it_on_is_unaffected(self, app: Any, fleet: Any) -> None:
        response = _pedir(app, fleet, impersonated=False)

        assert response.status_code == status.HTTP_200_OK

