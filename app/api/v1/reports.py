"""Reportes: payload listo para generar PDF/Excel en el frontend."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentFleet
from app.dependencies.influx import get_influx_repository
from app.dependencies.tariff import get_tariff_config
from app.repositories.scoped import ScopedInfluxRepository
from app.schemas.common import ApiResponse
from app.schemas.reports import ReportData
from app.schemas.tariff import TariffConfig
from app.services.periods import EnergyBucket
from app.services.reports.builder import build_report

router = APIRouter(prefix="/reports", tags=["Reports"])

#: Cada cuánto se agrupan las barras de energía y de costo del reporte. Sin
#: valor, cada periodo usa el suyo (1 h el día, 1 d el resto; una escalera por
#: duración en los rangos libres). Sirve para pedir MÁS detalle del que se
#: muestra por defecto — "estos 30 días, hora por hora"—, y no cambia ningún
#: total: solo en cuántas barras se reparte.
BucketQuery = Annotated[
    EnergyBucket | None,
    Query(description="Agrupación de las barras de energía: hour, day o week"),
]

RepoDep = Annotated[ScopedInfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
TariffDep = Annotated[TariffConfig, Depends(get_tariff_config)]


@router.get("/daily", summary="Reporte diario", response_model=ApiResponse[ReportData])
async def report_daily(
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    device_id: str | None = None,
    bucket: BucketQuery = None,
) -> ApiResponse[ReportData]:
    """Reporte del día en curso: consumo, exportación, KPIs y analytics."""
    return ApiResponse(
        data=await build_report(repo, settings, "daily", device_id, tariff, bucket=bucket)
    )


@router.get("/weekly", summary="Reporte semanal", response_model=ApiResponse[ReportData])
async def report_weekly(
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    device_id: str | None = None,
    bucket: BucketQuery = None,
) -> ApiResponse[ReportData]:
    """Reporte de la semana en curso (lunes a hoy)."""
    return ApiResponse(
        data=await build_report(repo, settings, "weekly", device_id, tariff, bucket=bucket)
    )


@router.get("/monthly", summary="Reporte mensual", response_model=ApiResponse[ReportData])
async def report_monthly(
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    device_id: str | None = None,
    bucket: BucketQuery = None,
) -> ApiResponse[ReportData]:
    """Reporte del mes en curso."""
    return ApiResponse(
        data=await build_report(repo, settings, "monthly", device_id, tariff, bucket=bucket)
    )


@router.get("/yearly", summary="Reporte anual", response_model=ApiResponse[ReportData])
async def report_yearly(
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    device_id: str | None = None,
    bucket: BucketQuery = None,
) -> ApiResponse[ReportData]:
    """Reporte del año en curso."""
    return ApiResponse(
        data=await build_report(repo, settings, "yearly", device_id, tariff, bucket=bucket)
    )


@router.get(
    "/custom",
    summary="Reporte de rango personalizado",
    response_model=ApiResponse[ReportData],
    responses={400: {"description": "Rango inválido"}},
)
async def report_custom(
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    from_: Annotated[datetime, Query(alias="from", description="Inicio del rango (UTC)")],
    to: Annotated[datetime, Query(description="Fin del rango (UTC)")],
    device_id: str | None = None,
    bucket: BucketQuery = None,
) -> ApiResponse[ReportData]:
    """Reporte de un rango de fechas arbitrario."""
    if from_ >= to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'from' debe ser anterior a 'to'")
    return ApiResponse(
        data=await build_report(repo, settings, "custom", device_id, tariff, from_, to, bucket)
    )
