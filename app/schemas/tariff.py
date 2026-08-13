"""Modelos de tarifa eléctrica (mercado regulado, ej. EPM) y costo en COP.

La tarifa (CU, excedente) cambia mes a mes por regulación CREG — se guarda
un historial por mes de vigencia para que el costo de un mes pasado use la
tarifa que realmente aplicaba entonces, no la tarifa actual.

Sin cargo fijo: este mercado no lo cobra, no es un campo omitido a propósito
en 0 — el concepto no existe acá.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

CostPeriod = Literal["day", "week", "month", "year", "custom"]


class TariffPeriod(BaseModel):
    """Tarifa vigente para un mes calendario ("YYYY-MM").

    `excedente_cop_kwh` es el precio del TRAMO 2 del excedente exportado —
    ver `app/services/tariff/cost.py::_accumulate` para la fórmula completa
    de dos tramos. Cambia mes a mes junto con `cu_cop_kwh` (misma
    regulación CREG), por eso vive acá y no en `TariffConfig`.
    """

    month: str = Field(examples=["2026-06"])
    cu_cop_kwh: float = Field(description="Costo unitario, rango > CS (COP/kWh)")
    excedente_cop_kwh: float = Field(
        description="Precio del tramo 2 del excedente exportado (COP/kWh) — "
        "el tramo 1, hasta lo importado en el mismo mes, se paga a cu_cop_kwh"
    )

    @field_validator("month")
    @classmethod
    def _valid_month(cls, v: str) -> str:
        if not _MONTH_RE.match(v):
            raise ValueError("month debe tener formato YYYY-MM")
        return v


class TariffConfig(BaseModel):
    umbral_cs_kwh: float = Field(
        default=130.0, description="Umbral de consumo subsidiado (kWh/mes)"
    )
    periods: list[TariffPeriod] = Field(default_factory=lambda: [])


class CostPoint(BaseModel):
    """Costo/crédito de un solo bucket (misma base que los costos de /reports y /costs/range)."""

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
    net_cost_cop: float  # consumption_cost - export_credit
    months_used: list[str]
    stale_months: list[str] = Field(
        default_factory=lambda: [],
        description="Meses del rango sin tarifa registrada; se usó la más reciente anterior",
    )
    series: list[CostPoint] = Field(default_factory=lambda: [])


class EfficiencyRecommendation(BaseModel):
    """Cuánto se habría ahorrado en COP si el excedente exportado del TRAMO 2
    (lo exportado por encima de lo importado en el mismo mes — el tramo 1 ya
    se paga al mismo precio que importar, ahí no hay nada que ganar) se
    hubiera autoconsumido en vez de exportado, a `cu_cop_kwh -
    excedente_cop_kwh` por kWh. Es una cota superior ilustrativa — asume que
    TODO ese tramo 2 se pudo desplazar a consumo, no una promesa exacta."""

    tariff_month: str
    stale: bool
    cu_cop_kwh: float
    excedente_cop_kwh: float
    export_kwh: float
    potential_savings_cop: float
