"""Modelos de /reports — payload listo para PDF/Excel en el frontend."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.analytics import BaseLoadResult, LoadFactorResult, MaxDemandResult
from app.schemas.energy import EnergyPoint
from app.schemas.kpis import KpiSummary
from app.schemas.tariff import CostBreakdown

ReportPeriod = Literal["daily", "weekly", "monthly", "yearly", "custom"]


class ReportData(BaseModel):
    report_type: ReportPeriod
    device_id: str | None
    period_start: datetime
    period_end: datetime
    consumption_kwh: float
    export_kwh: float
    net_balance_kwh: float  # importado - exportado
    consumption_series: list[EnergyPoint]
    export_series: list[EnergyPoint]
    kpis: KpiSummary
    max_demand: MaxDemandResult
    load_factor: LoadFactorResult
    base_load: BaseLoadResult
    costs: CostBreakdown
    generated_at: datetime
