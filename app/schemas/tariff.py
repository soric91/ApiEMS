"""Modelos de tarifa eléctrica (mercado regulado, ej. EPM) y costo en COP.

La tarifa (CU, cargo fijo) cambia mes a mes por regulación CREG — se guarda
un historial por mes de vigencia para que el costo de un mes pasado use la
tarifa que realmente aplicaba entonces, no la tarifa actual.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

CostPeriod = Literal["day", "week", "month", "year", "custom"]


class TariffPeriod(BaseModel):
    """Tarifa vigente para un mes calendario ("YYYY-MM")."""

    month: str = Field(examples=["2026-06"])
    cu_cop_kwh: float = Field(description="Costo unitario, rango > CS (COP/kWh)")
    cargo_fijo_cop: float = Field(description="Cargo fijo mensual (COP/factura)")

    @field_validator("month")
    @classmethod
    def _valid_month(cls, v: str) -> str:
        if not _MONTH_RE.match(v):
            raise ValueError("month debe tener formato YYYY-MM")
        return v


class TariffConfig(BaseModel):
    excedente_cop_kwh: float = Field(description="Crédito por energía exportada a la red (COP/kWh)")
    umbral_cs_kwh: float = Field(
        default=130.0, description="Umbral de consumo subsidiado (kWh/mes)"
    )
    periods: list[TariffPeriod] = Field(default_factory=lambda: [])


class CostPoint(BaseModel):
    """Costo/crédito de un solo bucket (mismo bucket que /consumption y /export)."""

    time: datetime
    consumption_kwh: float
    export_kwh: float
    consumption_cost_cop: float
    export_credit_cop: float
    net_cost_cop: float


class CostBreakdown(BaseModel):
    period: CostPeriod
    device_id: str | None
    period_start: datetime
    period_end: datetime
    consumption_kwh: float
    export_kwh: float
    consumption_cost_cop: float
    export_credit_cop: float
    cargo_fijo_cop: float
    net_cost_cop: float  # consumption_cost + cargo_fijo - export_credit
    cargo_fijo_included: bool  # False en day/week: no tiene sentido prorratearlo
    months_used: list[str]
    stale_months: list[str] = Field(
        default_factory=lambda: [],
        description="Meses del rango sin tarifa registrada; se usó la más reciente anterior",
    )
    series: list[CostPoint] = Field(default_factory=lambda: [])


class EfficiencyRecommendation(BaseModel):
    """Cuánto se habría ahorrado en COP si toda la energía exportada este
    mes se hubiera autoconsumido en vez de exportado, al precio de tarifa
    vigente (`cu_cop_kwh - excedente_cop_kwh` por kWh). Es una cota superior
    ilustrativa — asume que TODO lo exportado se pudo desplazar a consumo,
    no una promesa de ahorro exacto."""

    tariff_month: str
    stale: bool
    cu_cop_kwh: float
    excedente_cop_kwh: float
    export_kwh: float
    potential_savings_cop: float
