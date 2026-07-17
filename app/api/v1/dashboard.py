"""Panel principal: instantáneas desde RAM (MQTT) + energía del periodo desde InfluxDB."""

import asyncio
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentUser
from app.dependencies.influx import get_influx_repository
from app.dependencies.realtime import get_realtime_state
from app.models.variables import Variable
from app.repositories.influx import InfluxRepository
from app.schemas.common import ApiResponse
from app.schemas.dashboard import DashboardCard, DashboardData, DashboardStatus
from app.schemas.realtime import DeviceSnapshot
from app.services.influx.cache import cached_energy_total
from app.services.influx.client import InfluxService
from app.services.mqtt.client import MQTTService
from app.services.realtime.state import RealtimeState
from app.utils.period import start_of_day, start_of_month

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

RepoDep = Annotated[InfluxRepository, Depends(get_influx_repository)]
StateDep = Annotated[RealtimeState, Depends(get_realtime_state)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

STALE_AFTER_SECONDS = 90


def _pick_device(state: RealtimeState, device_id: str | None) -> DeviceSnapshot:
    if device_id is not None:
        snapshot = state.device(device_id)
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Sin datos en memoria para device_id={device_id!r}",
            )
        return snapshot
    snapshots = state.latest()
    if not snapshots:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sin datos en tiempo real todavía (esperando primer mensaje MQTT)",
        )
    return snapshots[0]


async def _build_dashboard(
    repo: InfluxRepository,
    state: RealtimeState,
    settings: Settings,
    device_id: str | None,
) -> DashboardData:
    snapshot = _pick_device(state, device_id)
    day_start = start_of_day(settings.TIMEZONE)
    month_start = start_of_month(settings.TIMEZONE)
    now = datetime.now(tz=UTC)

    consumption_today, consumption_month, export_today, export_month = await asyncio.gather(
        cached_energy_total(
            repo, Variable.POWER_ACTIVE_TOTAL_POS, day_start, now, snapshot.device_id
        ),
        cached_energy_total(
            repo, Variable.POWER_ACTIVE_TOTAL_POS, month_start, now, snapshot.device_id
        ),
        cached_energy_total(
            repo, Variable.POWER_ACTIVE_TOTAL_NEG, day_start, now, snapshot.device_id
        ),
        cached_energy_total(
            repo, Variable.POWER_ACTIVE_TOTAL_NEG, month_start, now, snapshot.device_id
        ),
    )

    data = snapshot.data
    return DashboardData(
        device_id=snapshot.device_id,
        power_active_total_w=data.get(Variable.POWER_ACTIVE_INST_TOTAL.value, 0.0),
        voltage_a=data.get(Variable.VOLTAGE_A.value, 0.0),
        voltage_b=data.get(Variable.VOLTAGE_B.value, 0.0),
        current_a=data.get(Variable.CURRENT_A.value, 0.0),
        current_b=data.get(Variable.CURRENT_B.value, 0.0),
        power_factor=data.get(Variable.FACTOR_POTENCIA_TOTAL.value, 0.0),
        consumption_today_kwh=consumption_today,
        consumption_month_kwh=consumption_month,
        export_today_kwh=export_today,
        export_month_kwh=export_month,
        last_update=snapshot.timestamp,
    )


@router.get(
    "",
    summary="Panel principal",
    response_model=ApiResponse[DashboardData],
    responses={
        404: {"description": "device_id sin datos en memoria"},
        503: {"description": "Sin datos en tiempo real todavía"},
    },
)
async def dashboard(
    repo: RepoDep,
    state: StateDep,
    settings: SettingsDep,
    _user: CurrentUser,
    device_id: str | None = None,
) -> ApiResponse[DashboardData]:
    """Potencia/voltaje/corriente/factor de potencia actuales (RAM, vía MQTT) +
    consumo y exportación de hoy y del mes (InfluxDB, `spread()` sobre los
    contadores). Si no se indica `device_id`, usa el primer dispositivo activo.
    """
    return ApiResponse(data=await _build_dashboard(repo, state, settings, device_id))


@router.get(
    "/cards",
    summary="Tarjetas resumidas del dashboard",
    response_model=ApiResponse[list[DashboardCard]],
)
async def dashboard_cards(
    repo: RepoDep,
    state: StateDep,
    settings: SettingsDep,
    _user: CurrentUser,
    device_id: str | None = None,
) -> ApiResponse[list[DashboardCard]]:
    """Mismos datos que `/dashboard`, formateados como lista de tarjetas
    `{key, label, value, unit}` listas para renderizar en el frontend.
    """
    d = await _build_dashboard(repo, state, settings, device_id)
    cards = [
        DashboardCard(key="power", label="Potencia actual", value=d.power_active_total_w, unit="W"),
        DashboardCard(key="voltage_a", label="Voltaje A", value=d.voltage_a, unit="V"),
        DashboardCard(key="voltage_b", label="Voltaje B", value=d.voltage_b, unit="V"),
        DashboardCard(key="current_a", label="Corriente A", value=d.current_a, unit="A"),
        DashboardCard(key="current_b", label="Corriente B", value=d.current_b, unit="A"),
        DashboardCard(
            key="power_factor", label="Factor de potencia", value=d.power_factor, unit=""
        ),
        DashboardCard(
            key="consumption_today", label="Consumo hoy", value=d.consumption_today_kwh, unit="kWh"
        ),
        DashboardCard(
            key="consumption_month",
            label="Consumo mes",
            value=d.consumption_month_kwh,
            unit="kWh",
        ),
        DashboardCard(
            key="export_today", label="Exportado hoy", value=d.export_today_kwh, unit="kWh"
        ),
        DashboardCard(
            key="export_month", label="Exportado mes", value=d.export_month_kwh, unit="kWh"
        ),
    ]
    return ApiResponse(data=cards)


@router.get(
    "/status",
    summary="Estado de conectividad",
    response_model=ApiResponse[DashboardStatus],
)
async def dashboard_status(
    request: Request, state: StateDep, _user: CurrentUser
) -> ApiResponse[DashboardStatus]:
    """Estado de MQTT, InfluxDB y frescura de los dispositivos en memoria.

    Un dispositivo se considera `online` si reportó en los últimos
    90 segundos; más allá de eso, su último valor sigue disponible en
    `/realtime` pero se considera obsoleto.
    """
    influx = cast(InfluxService, request.app.state.influx)
    mqtt = cast(MQTTService, request.app.state.mqtt)

    now = datetime.now(tz=UTC)
    snapshots = state.latest()
    online = [s for s in snapshots if (now - s.received_at).total_seconds() < STALE_AFTER_SECONDS]
    last_message_at = max((s.received_at for s in snapshots), default=None)

    return ApiResponse(
        data=DashboardStatus(
            mqtt_connected=mqtt.connected,
            influx_connected=await influx.ping(),
            devices_online=len(online),
            devices_total=len(snapshots),
            last_message_at=last_message_at,
        )
    )
