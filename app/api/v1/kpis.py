"""KPIs: estadísticas instantáneas + energía por periodo (Polars)."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentUser
from app.dependencies.influx import get_influx_repository
from app.repositories.influx import InfluxRepository
from app.schemas.common import ApiResponse
from app.schemas.kpis import KpiSummary
from app.services.kpis.summary import compute_kpis
from app.utils.period import start_of_day

router = APIRouter(prefix="/kpis", tags=["KPIs"])

RepoDep = Annotated[InfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get(
    "",
    summary="KPIs del periodo",
    response_model=ApiResponse[KpiSummary],
    deprecated=True,  # usar reports.kpis en /reports/{daily,weekly,monthly,yearly,custom}
)
async def kpis(
    repo: RepoDep,
    settings: SettingsDep,
    _user: CurrentUser,
    from_: Annotated[
        datetime | None,
        Query(alias="from", description="Inicio del rango (UTC). Por defecto: hoy."),
    ] = None,
    to: Annotated[
        datetime | None, Query(description="Fin del rango (UTC). Por defecto: ahora.")
    ] = None,
    device_id: str | None = None,
) -> ApiResponse[KpiSummary]:
    """Potencia (promedio/máx), voltaje (promedio/mín/máx), corriente
    (promedio) y factor de potencia (promedio) del periodo (por defecto:
    hoy), calculados con Polars sobre las series instantáneas; más consumo
    diario/semanal/mensual y exportación diaria/mensual.
    """
    now = datetime.now(tz=UTC)
    start = from_ or start_of_day(settings.TIMEZONE, now)
    stop = to or now
    if start >= stop:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'from' debe ser anterior a 'to'")
    return ApiResponse(data=await compute_kpis(repo, settings, start, stop, device_id))
