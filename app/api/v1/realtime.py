"""Tiempo real desde memoria (RAM) — NUNCA consulta InfluxDB."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies.auth import CurrentFleet
from app.dependencies.realtime import get_realtime_state
from app.schemas.common import ApiResponse
from app.schemas.realtime import DeviceSnapshot
from app.services.realtime.state import RealtimeState

router = APIRouter(prefix="/realtime", tags=["Realtime"])

StateDep = Annotated[RealtimeState, Depends(get_realtime_state)]


@router.get(
    "/latest",
    summary="Último snapshot de todos los dispositivos",
    response_model=ApiResponse[list[DeviceSnapshot]],
)
async def latest(state: StateDep, fleet: CurrentFleet) -> ApiResponse[list[DeviceSnapshot]]:
    """Último valor conocido (en RAM) de cada equipo de esta empresa.

    El estado en memoria es de toda la flota — la ingesta MQTT no distingue
    clientes y no debería, porque las alertas corren igual sin nadie mirando.
    El recorte pasa acá, al leerlo.
    """
    return ApiResponse(
        data=[snap for snap in state.latest() if snap.device_id in fleet.device_ids]
    )


@router.get(
    "/device",
    summary="Último snapshot de un dispositivo",
    response_model=ApiResponse[DeviceSnapshot],
    responses={404: {"description": "Dispositivo sin datos en memoria"}},
)
async def device(
    device_id: str, state: StateDep, fleet: CurrentFleet
) -> ApiResponse[DeviceSnapshot]:
    """Último valor conocido (en RAM) de un dispositivo específico."""
    # Un equipo ajeno se responde igual que uno inexistente: distinguirlos
    # confirmaría que otra empresa lo tiene.
    snapshot = state.device(device_id) if device_id in fleet.device_ids else None
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sin datos en memoria para device_id={device_id!r}",
        )
    return ApiResponse(data=snapshot)
