"""Modelos de /analytics.

Los indicadores de carga (max-demand, load-factor, base-load) se calculan
SOLO sobre las muestras donde POWER_ACTIVE_INST_TOTAL > 0 (importación de
la red). El sistema no mide consumo bruto de la casa (no hay medidor en el
inversor solar): durante exportación esos indicadores no están definidos,
así que esas ventanas se excluyen en vez de inventar un proxy.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.tariff import EfficiencyRecommendation

# Cómo se lee el medidor de frontera. Vive en los esquemas, como `CostPeriod`:
# lo usan tanto el servicio que lo resuelve como el contrato de la API.
SiteMode = Literal["consumo", "generacion"]


class SiteModeResult(BaseModel):
    """Cómo hay que leer el medidor de frontera de esta sede.

    `consumo`: sin generación propia, todo lo que pasa por el medidor es
    consumo y los indicadores valen las 24 h. `generacion`: hay fotovoltaica
    inyectando, el medidor solo ve el balance neto y varios indicadores solo
    son válidos fuera de las horas de sol.

    `source` dice de dónde salió: `crm` si alguien lo declaró en la sede,
    `detected` si se dedujo de la energía exportada del último mes.
    """

    device_id: str | None
    mode: SiteMode
    source: Literal["crm", "detected"]


class HeatmapResult(BaseModel):
    """Cuadrícula hora x día: `values[i][h]` es la casilla del día `dates[i]`
    a la hora local `h` (0..23).

    Un `null` es una hora SIN DATO, no un cero: pintar como "consumo cero" las
    horas en que el gateway estuvo caído es justo el error que
    `/analytics/coverage` existe para evitar.

    `cost` es lo que costó la importación de esa hora, no el neto: el crédito
    por exportar se reparte en dos tramos que se resuelven por mes calendario
    (ver `services/tariff/cost.py`), y repartirlo hora a hora daría un número
    que no suma a la factura.
    """

    device_id: str | None
    period_start: datetime
    period_end: datetime
    metric: Literal["import", "export", "net", "cost"]
    unit: str
    dates: list[str]
    values: list[list[float | None]]


class CoveragePoint(BaseModel):
    """Cuánto dato llegó en una ventana. `ratio` 1.0 = completa."""

    time: datetime
    samples: int
    ratio: float


class CoverageResult(BaseModel):
    """Cuánto dato hay realmente en el rango.

    Un hueco no es consumo cero, pero se ve igual en una gráfica: sin esto, un
    gateway caído diez horas deja un día que parece de bajo consumo. Todo lo
    que compara periodos entre sí depende de saber que ambos están completos.

    `expected_source` dice de dónde salieron las muestras esperadas:
    `declarado` (el intervalo de lectura configurado en el CRM), `inferido`
    (el percentil 90 de las ventanas del propio rango) o `desconocido` (no hubo
    ninguna muestra de la cual inferir).
    """

    device_id: str | None
    period_start: datetime
    period_end: datetime
    bucket_seconds: int
    expected_per_bucket: float | None
    expected_source: Literal["declarado", "inferido", "desconocido"]
    overall_ratio: float | None
    incomplete_buckets: int
    points: list[CoveragePoint]


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
