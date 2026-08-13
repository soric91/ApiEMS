"""Qué medidores tiene este cliente.

Existe porque el panel armaba su lista de medidores con el último valor en
memoria de cada equipo que publicó **desde que ApiEMS arrancó**. Con esa fuente
pasaban tres cosas malas:

* Al reiniciar ApiEMS el selector quedaba vacío hasta que llegara la primera
  lectura de cada equipo.
* Un gateway caído hacía desaparecer sus medidores. No se podían elegir ni
  consultar su histórico, que sí existe en InfluxDB.
* No había forma de agrupar: el estado en memoria no sabe de sedes ni de
  gateways, solo de equipos sueltos.

El inventario es del CRM, que es donde se dan de alta. Esto lo devuelve tal
cual, acotado a la empresa del token.
"""

from fastapi import APIRouter

from app.dependencies.auth import CurrentFleet
from app.schemas.common import ApiResponse
from app.schemas.devices import DeviceDisponible

router = APIRouter(prefix="/devices", tags=["Devices"])


@router.get(
    "",
    summary="Los medidores de este cliente",
    response_model=ApiResponse[list[DeviceDisponible]],
)
async def list_devices(fleet: CurrentFleet) -> ApiResponse[list[DeviceDisponible]]:
    """Todos los que están dados de alta, tengan datos o no.

    Vienen ordenados por sede, gateway y nombre — el mismo orden del árbol del
    CRM, para que quien mire los dos vea lo mismo en el mismo lugar.
    """
    return ApiResponse(
        data=[
            DeviceDisponible(
                device_id=device.id,
                nombre=device.nombre,
                modbus_id=device.modbus_id,
                sede_id=device.sede_id,
                sede=device.sede,
                gateway_id=device.gateway_id,
                gateway=device.gateway,
                gateway_en_linea=device.gateway_en_linea,
            )
            for device in fleet.devices
        ]
    )
