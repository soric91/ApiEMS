"""Modelos de /analytics.

Los indicadores de carga (max-demand, load-factor, base-load) se calculan
SOLO sobre las muestras donde POWER_ACTIVE_INST_TOTAL > 0 (importación de
la red). El sistema no mide consumo bruto de la casa (no hay medidor en el
inversor solar): durante exportación esos indicadores no están definidos,
así que esas ventanas se excluyen en vez de inventar un proxy.
"""

from datetime import datetime

from pydantic import BaseModel

from app.schemas.tariff import EfficiencyRecommendation


class HourProfilePoint(BaseModel):
    hour: int
    power_avg_w: float
    power_max_w: float
    power_min_w: float
    sample_count: int


class WeekdayProfilePoint(BaseModel):
    weekday: int  # 0=lunes .. 6=domingo
    weekday_name: str
    consumption_avg_kwh: float
    export_avg_kwh: float


class MaxDemandResult(BaseModel):
    period_start: datetime
    period_end: datetime
    device_id: str | None
    peak_power_w: float | None
    peak_at: datetime | None


class LoadFactorResult(BaseModel):
    period_start: datetime
    period_end: datetime
    device_id: str | None
    average_import_w: float | None
    peak_import_w: float | None
    load_factor: float | None  # average/peak, 0..1; None si no hubo importación


class BaseLoadResult(BaseModel):
    period_start: datetime
    period_end: datetime
    device_id: str | None
    percentile: float
    base_load_w: float | None  # None si no hubo importación en el periodo


class ComparePeriod(BaseModel):
    period_start: datetime
    period_end: datetime
    consumption_kwh: float
    export_kwh: float


class CompareResult(BaseModel):
    device_id: str | None
    period_a: ComparePeriod
    period_b: ComparePeriod
    consumption_delta_pct: float | None
    export_delta_pct: float | None


class AnalyticsSummary(BaseModel):
    """Resumen general para exportar (ej. PDF desde el frontend): consumo y
    exportación diario/semanal/mensual, patrón horario típico, hora de mayor
    consumo y de mayor exportación, y la recomendación de eficiencia."""

    period_start: datetime
    period_end: datetime
    device_id: str | None
    consumption_daily_kwh: float
    consumption_weekly_kwh: float
    consumption_monthly_kwh: float
    export_daily_kwh: float
    export_monthly_kwh: float
    hourly_profile: list[HourProfilePoint]
    peak_consumption_hour: int | None
    peak_export_hour: int | None
    efficiency: EfficiencyRecommendation | None


class ReactiveQuadrantPoint(BaseModel):
    """Energía reactiva (kvarh) de cada cuadrante en una ventana del período."""

    time: datetime
    q1_kvarh: float
    q2_kvarh: float
    q3_kvarh: float
    q4_kvarh: float


class ReactiveQuadrantsResult(BaseModel):
    """Energía reactiva (kvarh) del período por cuadrante.

    Q1/Q2 es reactiva importada de la red (Q1 inductiva, Q2 capacitiva);
    Q3/Q4 es reactiva exportada a la red (Q3 capacitiva, Q4 inductiva) — IEC
    60375. `dominant` es el cuadrante con más energía del período, o None si
    no hubo ninguna. `balance` positivo significa que la red le entrega
    reactiva al cliente.
    """

    period_start: datetime
    period_end: datetime
    device_id: str | None
    q1_kvarh: float
    q2_kvarh: float
    q3_kvarh: float
    q4_kvarh: float
    total_import_kvarh: float  # q1 + q2
    total_export_kvarh: float  # q3 + q4
    balance_kvarh: float  # importado - exportado
    dominant: str | None  # "q1" | "q2" | "q3" | "q4"
    dominant_kvarh: float
    trend: list[ReactiveQuadrantPoint]
