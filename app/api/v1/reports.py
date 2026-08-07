"""Reportes: payload listo para generar PDF/Excel en el frontend."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentUser
from app.dependencies.influx import get_influx_repository
from app.dependencies.tariff import get_tariff_config
from app.repositories.influx import InfluxRepository
from app.schemas.common import ApiResponse
from app.schemas.reports import ReportData
from app.schemas.tariff import TariffConfig
from app.services.reports.builder import build_report

router = APIRouter(prefix="/reports", tags=["Reports"])

RepoDep = Annotated[InfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
TariffDep = Annotated[TariffConfig, Depends(get_tariff_config)]


@router.get("/daily", summary="Reporte diario", response_model=ApiResponse[ReportData])
async def report_daily(
    repo: RepoDep, settings: SettingsDep, tariff: TariffDep, _user: CurrentUser,
    device_id: str | None = None,
) -> ApiResponse[ReportData]:
    """Reporte del día en curso: consumo, exportación, KPIs y analytics."""
    return ApiResponse(data=await build_report(repo, settings, "daily", device_id, tariff))


@router.get("/weekly", summary="Reporte semanal", response_model=ApiResponse[ReportData])
async def report_weekly(
    repo: RepoDep, settings: SettingsDep, tariff: TariffDep, _user: CurrentUser,
    device_id: str | None = None,
) -> ApiResponse[ReportData]:
    """Reporte de la semana en curso (lunes a hoy)."""
    return ApiResponse(data=await build_report(repo, settings, "weekly", device_id, tariff))


@router.get("/monthly", summary="Reporte mensual", response_model=ApiResponse[ReportData])
async def report_monthly(
    repo: RepoDep, settings: SettingsDep, tariff: TariffDep, _user: CurrentUser,
    device_id: str | None = None,
) -> ApiResponse[ReportData]:
    """Reporte del mes en curso."""
    return ApiResponse(data=await build_report(repo, settings, "monthly", device_id, tariff))


@router.get("/yearly", summary="Reporte anual", response_model=ApiResponse[ReportData])
async def report_yearly(
    repo: RepoDep, settings: SettingsDep, tariff: TariffDep, _user: CurrentUser,
    device_id: str | None = None,
) -> ApiResponse[ReportData]:
    """Reporte del año en curso."""
    return ApiResponse(data=await build_report(repo, settings, "yearly", device_id, tariff))


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
    _user: CurrentUser,
    from_: Annotated[datetime, Query(alias="from", description="Inicio del rango (UTC)")],
    to: Annotated[datetime, Query(description="Fin del rango (UTC)")],
    device_id: str | None = None,
) -> ApiResponse[ReportData]:
    """Reporte de un rango de fechas arbitrario."""
    if from_ >= to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'from' debe ser anterior a 'to'")
    return ApiResponse(
        data=await build_report(repo, settings, "custom", device_id, tariff, from_, to)
    )
