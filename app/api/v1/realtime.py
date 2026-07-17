"""Tiempo real desde memoria (RAM) — NUNCA consulta InfluxDB."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies.auth import CurrentUser
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
async def latest(state: StateDep, _user: CurrentUser) -> ApiResponse[list[DeviceSnapshot]]:
    """Último valor conocido (en RAM) de cada dispositivo que ha publicado por MQTT."""
    return ApiResponse(data=state.latest())


@router.get(
    "/device",
    summary="Último snapshot de un dispositivo",
    response_model=ApiResponse[DeviceSnapshot],
    responses={404: {"description": "Dispositivo sin datos en memoria"}},
)
async def device(
    device_id: str, state: StateDep, _user: CurrentUser
) -> ApiResponse[DeviceSnapshot]:
    """Último valor conocido (en RAM) de un dispositivo específico."""
    snapshot = state.device(device_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sin datos en memoria para device_id={device_id!r}",
        )
    return ApiResponse(data=snapshot)
