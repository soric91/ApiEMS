"""`GET /api/v1/devices` — qué medidores tiene este cliente.

El endpoint existe porque el panel armaba su lista con el último valor en
memoria, que solo conoce lo que publicó desde que el proceso arrancó. Lo que
se prueba acá es sobre todo lo que esa fuente **no** podía dar: los equipos
de un gateway caído, y la sede y el gateway de cada uno.
"""

from typing import Any

from fastapi.testclient import TestClient

from app.services.crm.fleet import ClientFleet, FleetDevice
from tests.conftest import TEST_CLIENT_ID, TEST_DEVICE_ID

RUTA = "/api/v1/devices"


def _equipo(
    sufijo: str,
    *,
    sede: str = "Planta Norte",
    gateway: str = "GW-0001",
    gateway_en_linea: bool = True,
) -> FleetDevice:
    """Explícito y no `**cambios`: con un diccionario suelto el verificador no
    puede comprobar los tipos y cualquier campo mal escrito pasaría."""
    return FleetDevice(
        id=f"eq-{sufijo}",
        nombre=f"Medidor {sufijo}",
        modbus_id=10,
        sede_id="s1",
        sede=sede,
        gateway_id="g1",
        gateway=gateway,
        gateway_en_linea=gateway_en_linea,
    )


class TestListaCompleta:
    def test_devuelve_los_equipos_de_la_flota(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(RUTA, headers=auth_headers)

        assert response.status_code == 200
        assert [d["device_id"] for d in response.json()["data"]] == [TEST_DEVICE_ID]

    def test_dice_de_que_sede_y_gateway_es_cada_uno(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Sin esto el panel no puede agrupar, y con veinte medidores en una
        lista plana el selector es inservible."""
        equipo = client.get(RUTA, headers=auth_headers).json()["data"][0]

        assert equipo["sede"] == "Planta Norte"
        assert equipo["gateway"] == "GW-0001"
        assert equipo["gateway_en_linea"] is True

    def test_un_equipo_de_un_gateway_caido_igual_aparece(
        self, app: Any, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """La razón principal del endpoint.

        Con la fuente anterior —el estado en memoria— un gateway sin publicar
        hacía desaparecer sus medidores: no se podían elegir ni consultar su
        histórico, que sí está guardado. Existen, y se informa que su gateway
        está caído para que la ausencia de datos se explique.
        """
        app.state.fleet_directory = _directorio(
            _equipo("vivo"), _equipo("muerto", gateway="GW-0002", gateway_en_linea=False)
        )

        data = client.get(RUTA, headers=auth_headers).json()["data"]

        por_id = {d["device_id"]: d for d in data}
        assert set(por_id) == {"eq-vivo", "eq-muerto"}
        assert por_id["eq-muerto"]["gateway_en_linea"] is False

    def test_sin_token_lo_rechaza(self, client: TestClient) -> None:
        assert client.get(RUTA).status_code == 401


class TestElOrden:
    def test_va_por_sede_gateway_y_nombre(
        self, app: Any, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """El mismo orden del árbol del CRM.

        Se fija acá y no en el panel para que quien mire los dos vea lo mismo
        en el mismo lugar. El orden lo impone `FleetDirectory`, así que este
        test recibe la flota ya ordenada y solo comprueba que no se altere.
        """
        app.state.fleet_directory = _directorio(
            _equipo("a", sede="Planta Norte", gateway="GW-0001"),
            _equipo("b", sede="Planta Norte", gateway="GW-0002"),
            _equipo("c", sede="Planta Sur", gateway="GW-0003"),
        )

        data = client.get(RUTA, headers=auth_headers).json()["data"]

        assert [d["device_id"] for d in data] == ["eq-a", "eq-b", "eq-c"]


def _directorio(*equipos: FleetDevice) -> Any:
    flota = ClientFleet(
        client_id=TEST_CLIENT_ID,
        devices=equipos,
        variables=(),
        puede_ver_consumo=True,
    )

    class Doble:
        async def for_client(self, client_id: str) -> ClientFleet:
            return flota

    return Doble()
