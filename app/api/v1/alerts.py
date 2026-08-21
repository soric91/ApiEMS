"""Alertas de consumo anómalo — sin ML: bandas de percentiles (P10-P90)
sobre datos históricos reales. Ver app/services/analytics/anomaly.py.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentFleet
from app.dependencies.influx import get_influx_repository
from app.repositories.scoped import ScopedInfluxRepository
from app.schemas.alerts import Alert, AlertsHistory, AlertsResponse
from app.schemas.common import ApiResponse
from app.services.alerts.detector import check_daily_total
from app.services.alerts.history import alerts_history, hourly_anomalies
from app.services.alerts.state import AlertsState

router = APIRouter(prefix="/alerts", tags=["Alerts"])

# Cuánto historial se mira por defecto. Treinta días es el mismo horizonte con
# el que se arman las bandas (`ALERTS_BASELINE_DAYS`): pedir más días de los
# que sostienen la banda daría veredictos con menos respaldo del que aparentan.
HISTORY_LOOKBACK_DAYS = 30

#: Cuántos días de alertas horarias se reconstruyen para la campanita. Una
#: semana es lo que alguien revisa hacia atrás cuando vuelve el lunes; más allá
#: la pregunta ya es "qué pasó ese día" y para eso está `/alerts/history`.
RECIENTES_DIAS = 7

#: Cuántas alertas de memoria se miran antes de filtrar por flota. La lista
#: guarda las de todos los clientes del proceso, así que pedir solo `limit`
#: podría devolver menos de las que le tocan a este.
MAX_EN_MEMORIA = 200

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
    """`recent`: las alertas horarias de los últimos días.

    Salen de dos sitios que se complementan. Las de esta sesión están en RAM,
    con el segundo exacto en que se dispararon; las anteriores se RECALCULAN
    sobre InfluxDB con la misma función que evalúa la lectura en vivo. Antes
    solo existían las primeras, así que reiniciar el proceso borraba los avisos
    del cliente aunque los datos que los provocaron siguieran guardados.

    La reconstrucción exige `device_id`: una alerta sin equipo al que atribuirla
    no sirve para nada, y recorrer la flota entera sería un par de consultas por
    equipo en cada visita.

    Cuando una hora aparece por los dos caminos gana la de memoria: es la que
    de verdad se emitió, con su valor y su hora exactos.

    `daily_total`: comparación bajo demanda del último día completo (ayer)
    contra su banda histórica por día de semana; `null` si no hay suficiente
    historial o el consumo de ayer estuvo dentro de lo esperado.
    """
    # La reconstrucción es POR MEDIDOR: una alerta sin equipo no se puede
    # atribuir, y reconstruir la flota entera sería un par de consultas por
    # cada equipo en cada visita al panel. Sin `device_id` —que el panel
    # siempre manda, porque siempre hay uno seleccionado— quedan solo las de
    # esta sesión.
    daily, reconstruidas = await asyncio.gather(
        check_daily_total(repo, settings, device_id),
        hourly_anomalies(repo, settings, device_id, RECIENTES_DIAS)
        if device_id is not None
        else _sin_reconstruir(),
    )

    # La lista en memoria es de TODA la flota del proceso: sin acotarla, un
    # cliente vería las alertas de los medidores de otro.
    en_memoria = [
        a
        for a in state.recent(limit=MAX_EN_MEMORIA)
        if a.device_id in fleet.device_ids and (device_id is None or a.device_id == device_id)
    ]

    return ApiResponse(
        data=AlertsResponse(recent=_unidas(en_memoria, reconstruidas, limit), daily_total=daily)
    )


async def _sin_reconstruir() -> list[Alert]:
    return []


def _hora_de(alerta: Alert) -> tuple[str | None, int]:
    """La identidad de una alerta horaria: qué medidor y qué hora concreta."""
    return (alerta.device_id, int(alerta.timestamp.timestamp() // 3600))


def _unidas(en_memoria: list[Alert], reconstruidas: list[Alert], limit: int) -> list[Alert]:
    """Las dos fuentes en una sola lista, sin repetir la misma hora.

    Manda la de memoria: es la alerta tal como se emitió. La reconstruida es
    una reconstrucción fiel del veredicto, pero con el pico de la hora en vez
    del valor exacto que disparó el aviso.
    """
    vistas = {_hora_de(a) for a in en_memoria}
    unidas = en_memoria + [a for a in reconstruidas if _hora_de(a) not in vistas]
    unidas.sort(key=lambda a: a.timestamp, reverse=True)
    return unidas[:limit]
