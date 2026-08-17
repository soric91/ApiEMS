"""Alertas de consumo anómalo — sin ML: bandas de percentiles (P10-P90)
sobre datos históricos reales. Ver app/services/analytics/anomaly.py.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentFleet
from app.dependencies.influx import get_influx_repository
from app.repositories.scoped import ScopedInfluxRepository
from app.schemas.alerts import AlertsHistory, AlertsResponse
from app.schemas.common import ApiResponse
from app.services.alerts.detector import check_daily_total
from app.services.alerts.history import alerts_history
from app.services.alerts.state import AlertsState

router = APIRouter(prefix="/alerts", tags=["Alerts"])

# Cuánto historial se mira por defecto. Treinta días es el mismo horizonte con
# el que se arman las bandas (`ALERTS_BASELINE_DAYS`): pedir más días de los
# que sostienen la banda daría veredictos con menos respaldo del que aparentan.
HISTORY_LOOKBACK_DAYS = 30

RepoDep = Annotated[ScopedInfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_alerts_state(request: Request) -> AlertsState:
    return cast(AlertsState, request.app.state.alerts_state)


StateDep = Annotated[AlertsState, Depends(get_alerts_state)]


@router.get(
    "/history",
    summary="Historial de anomalías y cambios de nivel",
    response_model=ApiResponse[AlertsHistory],
)
async def alerts_history_endpoint(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    from_: Annotated[
        datetime | None, Query(alias="from", description="Inicio del rango (UTC)")
    ] = None,
    to: Annotated[datetime | None, Query(description="Fin del rango (UTC)")] = None,
    device_id: str | None = None,
) -> ApiResponse[AlertsHistory]:
    """Qué días se salieron de lo normal, y desde cuándo cambió el nivel.

    La alerta en vivo solo habla de ayer y se pierde al reiniciar; acá se
    recalcula el rango entero sobre los datos guardados, así que la respuesta
    es la misma hoy que dentro de un mes.

    Además del día atípico, se reporta el cambio SOSTENIDO de consumo (CUSUM):
    lo que las bandas puntuales no ven, porque cada día por separado sigue
    cayendo dentro de lo normal mientras el promedio se corrió.

    Por defecto, los últimos 30 días completos.
    """
    now = datetime.now(tz=UTC)
    start = from_ or now - timedelta(days=HISTORY_LOOKBACK_DAYS)
    stop = to or now
    if start >= stop:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'from' debe ser anterior a 'to'")
    return ApiResponse(data=await alerts_history(repo, settings, start, stop, device_id))


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
