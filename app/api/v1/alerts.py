"""Alertas de consumo anómalo — sin ML: bandas de percentiles (P10-P90)
sobre datos históricos reales. Ver app/services/analytics/anomaly.py.
"""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentFleet
from app.dependencies.influx import get_influx_repository
from app.repositories.scoped import ScopedInfluxRepository
from app.schemas.alerts import AlertsResponse
from app.schemas.common import ApiResponse
from app.services.alerts.detector import check_daily_total
from app.services.alerts.state import AlertsState

router = APIRouter(prefix="/alerts", tags=["Alerts"])

RepoDep = Annotated[ScopedInfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_alerts_state(request: Request) -> AlertsState:
    return cast(AlertsState, request.app.state.alerts_state)


StateDep = Annotated[AlertsState, Depends(get_alerts_state)]


@router.get("", summary="Alertas recientes", response_model=ApiResponse[AlertsResponse])
async def alerts(
    repo: RepoDep,
    settings: SettingsDep,
    state: StateDep,
    fleet: CurrentFleet,
    device_id: str | None = None,
    limit: int = 50,
) -> ApiResponse[AlertsResponse]:
    """`recent`: alertas horarias generadas en tiempo real (RAM, se pierden
    al reiniciar el proceso — igual que el resto del estado en memoria).
    `daily_total`: comparación bajo demanda del último día completo (ayer)
    contra su banda histórica por día de semana; `null` si no hay suficiente
    historial o el consumo de ayer estuvo dentro de lo esperado.
    """
    daily = await check_daily_total(repo, settings, device_id)
    return ApiResponse(data=AlertsResponse(recent=state.recent(limit), daily_total=daily))
