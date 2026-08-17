"""Costo/crédito en COP a partir de energía importada/exportada + tarifa.

Regla de transparencia: si un mes del rango no tiene tarifa registrada, se
usa la más reciente anterior disponible y se marca en `stale_months` — nunca
se oculta que el número es una estimación con tarifa vieja.

Sin cargo fijo: este mercado no lo cobra, no hace falta prorratearlo ni
incluirlo por mes.

Excedente en dos tramos, POR MES CALENDARIO (nunca por bucket ni acumulado
entre meses distintos): lo exportado hasta el total importado en ese mismo
mes se paga al mismo precio que la importación (`cu_cop_kwh` — "tramo 1");
lo que sobra por encima, al precio de excedente de ESE mes
(`TariffPeriod.excedente_cop_kwh` — "tramo 2").

Los TOTALES (`consumption_cost`, `export_credit`) se calculan agregando por
mes sobre TODOS los puntos, sin depender de que un punto de exportación
tenga un punto de consumo exacto en el mismo instante — dos series que no
comparten exactamente los mismos timestamps igual dan un total correcto.
La `series` por bucket (para graficar) sí itera por punto de consumo y
empareja por timestamp — un punto de exportación sin match ahí simplemente
no aparece en el gráfico, pero cuenta en el total.

`_accumulate` es el núcleo puro (opera sobre listas de EnergyPoint, sin
saber de dónde vinieron) — lo comparten `/costs/{day,week,month,year}`,
`/costs/range` y el bloque de costos embebido en /reports, para no repetir
la lógica ni hacer llamadas extra a InfluxDB donde los puntos ya existen.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.schemas.energy import EnergySummary
from app.schemas.influx import EnergyPoint
from app.schemas.tariff import CostBreakdown, CostPeriod, CostPoint, TariffConfig, TariffPeriod

_MONTHS_PER_YEAR = 12


def _month_key(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _months_in_range(start: datetime, end: datetime) -> list[str]:
    """Meses calendario que toca [start, end) — un mes sin ningún punto de
    datos igual tiene que evaluarse contra la tarifa (y marcarse stale si no
    hay una) en vez de desaparecer en silencio porque no había kWh que sumar."""
    touched_end = end - timedelta(microseconds=1) if end > start else end
    months: list[str] = []
    year, month = start.year, start.month
    end_key = _month_key(touched_end)
    while True:
        key = f"{year:04d}-{month:02d}"
        months.append(key)
        if key >= end_key:
            break
        month += 1
        if month > _MONTHS_PER_YEAR:
            month = 1
            year += 1
    return months


def rate_for_month(config: TariffConfig, month: str) -> tuple[TariffPeriod | None, bool]:
    """(tarifa, is_stale). is_stale=True si no había tarifa exacta para ese
    mes y se usó la más reciente anterior disponible. Pública: también la
    usa `services/analytics/summary.py` para la recomendación de eficiencia."""
    exact = next((p for p in config.periods if p.month == month), None)
    if exact is not None:
        return exact, False
    earlier = sorted((p for p in config.periods if p.month < month), key=lambda p: p.month)
    return (earlier[-1], True) if earlier else (None, True)


def _sum_by_month(points: list[EnergyPoint]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for point in points:
        month = _month_key(point.time)
        totals[month] = totals.get(month, 0.0) + point.value
    return totals


@dataclass(frozen=True)
class MonthlyTotals:
    """Lo que sale de agregar el periodo mes a mes.

    El reparto en tramos viaja acá y no se recalcula afuera: es el mismo que
    produce el crédito, así que separarlo sería tener dos versiones de la misma
    resta esperando a discrepar."""

    consumption_cost: float
    export_credit: float
    tier1_kwh: float
    tier2_kwh: float
    tier1_credit: float
    tier2_credit: float
    months_used: set[str]
    stale_months: set[str]


def _reparto_mensual(points: list[EnergyPoint], total: float) -> dict[str, float]:
    """La energía de cada mes, ajustada para que sume exactamente `total`.

    Los puntos de la serie salen de un `difference()` POR VENTANA y el total de
    un `difference()` sobre el rango entero: no coinciden al decimal, porque
    los bordes de ventana reparten distinto. La diferencia es de milésimas,
    pero convertía el informe en algo que no cuadra consigo mismo — llegó a
    publicar 139.93 kWh en el tramo 1 con 139.61 kWh importados, y el tramo 1
    por definición no puede superar lo importado.

    El reparto entre meses se respeta (es la única fuente que lo sabe) y la
    escala la fija el total, que es la cifra que además se muestra."""
    por_mes = _sum_by_month(points)
    suma = sum(por_mes.values())
    if suma <= 0 or total <= 0:
        return por_mes
    factor = total / suma
    return {mes: valor * factor for mes, valor in por_mes.items()}


def _totals_by_month(
    config: TariffConfig,
    consumption_points: list[EnergyPoint],
    export_points: list[EnergyPoint],
    period_start: datetime,
    period_end: datetime,
    consumption_total: float,
    export_total: float,
) -> MonthlyTotals:
    """Costo, crédito y su reparto en tramos, agregado por mes sobre el total
    real importado/exportado ese mes.

    Itera sobre TODOS los meses que el periodo toca (`_months_in_range`), no
    solo los que tienen puntos — un mes sin datos igual necesita marcarse
    `stale_months` si no hay tarifa para él."""
    import_by_month = _reparto_mensual(consumption_points, consumption_total)
    export_by_month = _reparto_mensual(export_points, export_total)

    consumption_cost = 0.0
    export_credit = 0.0
    tier1_kwh_total = 0.0
    tier2_kwh_total = 0.0
    tier1_credit_total = 0.0
    tier2_credit_total = 0.0
    months_used: set[str] = set()
    stale_months: set[str] = set()

    months = set(import_by_month) | set(export_by_month) | set(
        _months_in_range(period_start, period_end)
    )
    for month in sorted(months):
        rate, stale = rate_for_month(config, month)
        if rate is None:
            stale_months.add(month)
            continue

        month_import = import_by_month.get(month, 0.0)
        month_export = export_by_month.get(month, 0.0)
        tier1_kwh = min(month_import, month_export)
        tier2_kwh = month_export - tier1_kwh

        tier1_credit = tier1_kwh * rate.cu_cop_kwh
        tier2_credit = tier2_kwh * rate.excedente_cop_kwh
        consumption_cost += month_import * rate.cu_cop_kwh
        export_credit += tier1_credit + tier2_credit
        tier1_kwh_total += tier1_kwh
        tier2_kwh_total += tier2_kwh
        tier1_credit_total += tier1_credit
        tier2_credit_total += tier2_credit
        months_used.add(rate.month)
        if stale:
            stale_months.add(month)

    return MonthlyTotals(
        consumption_cost=consumption_cost,
        export_credit=export_credit,
        tier1_kwh=tier1_kwh_total,
        tier2_kwh=tier2_kwh_total,
        tier1_credit=tier1_credit_total,
        tier2_credit=tier2_credit_total,
        months_used=months_used,
        stale_months=stale_months,
    )


def _series_by_bucket(
    config: TariffConfig,
    consumption_points: list[EnergyPoint],
    export_points: list[EnergyPoint],
) -> list[CostPoint]:
    """Serie para graficar — itera por punto de consumo, empareja exportación
    por timestamp exacto. Reparto de tramo 1/tramo 2 marginal (delta del
    mínimo acumulado dentro del mes): la serie de un mes suma exacto a su
    total real siempre que las series de consumo/exportación compartan
    timestamps; si no comparten alguno, ese punto de exportación no entra acá
    (pero sí en `_totals_by_month`)."""
    export_by_time = {p.time: p.value for p in export_points}
    running_import: dict[str, float] = {}
    running_export: dict[str, float] = {}
    series: list[CostPoint] = []

    for point in consumption_points:
        month = _month_key(point.time)
        rate, _ = rate_for_month(config, month)
        export_value = export_by_time.get(point.time, 0.0)

        if rate is None:
            consumption_cost_point = 0.0
            export_credit_point = 0.0
        else:
            consumption_cost_point = round(point.value * rate.cu_cop_kwh, 2)

            prev_tier1 = min(running_import.get(month, 0.0), running_export.get(month, 0.0))
            running_import[month] = running_import.get(month, 0.0) + point.value
            running_export[month] = running_export.get(month, 0.0) + export_value
            new_tier1 = min(running_import[month], running_export[month])

            tier1_kwh = new_tier1 - prev_tier1
            tier2_kwh = export_value - tier1_kwh
            export_credit_point = round(
                tier1_kwh * rate.cu_cop_kwh + tier2_kwh * rate.excedente_cop_kwh, 2
            )

        series.append(
            CostPoint(
                time=point.time,
                consumption_kwh=point.value,
                export_kwh=export_value,
                consumption_cost_cop=consumption_cost_point,
                export_credit_cop=export_credit_point,
                net_cost_cop=round(consumption_cost_point - export_credit_point, 2),
            )
        )

    return series


def compute_cost_from_points(
    config: TariffConfig,
    period: CostPeriod,
    period_start: datetime,
    period_end: datetime,
    device_id: str | None,
    consumption_points: list[EnergyPoint],
    export_points: list[EnergyPoint],
    consumption_total: float,
    export_total: float,
) -> CostBreakdown:
    totales = _totals_by_month(
        config,
        consumption_points,
        export_points,
        period_start,
        period_end,
        consumption_total,
        export_total,
    )
    series = _series_by_bucket(config, consumption_points, export_points)
    return CostBreakdown(
        period=period,
        device_id=device_id,
        period_start=period_start,
        period_end=period_end,
        consumption_kwh=consumption_total,
        export_kwh=export_total,
        consumption_cost_cop=round(totales.consumption_cost, 2),
        export_credit_cop=round(totales.export_credit, 2),
        net_cost_cop=round(totales.consumption_cost - totales.export_credit, 2),
        export_tier1_kwh=round(totales.tier1_kwh, 2),
        export_tier2_kwh=round(totales.tier2_kwh, 2),
        export_tier1_credit_cop=round(totales.tier1_credit, 2),
        export_tier2_credit_cop=round(totales.tier2_credit, 2),
        months_used=sorted(totales.months_used),
        stale_months=sorted(totales.stale_months),
        series=series,
    )


def compute_cost(
    config: TariffConfig,
    period: CostPeriod,
    consumption: EnergySummary,
    export: EnergySummary,
    device_id: str | None,
) -> CostBreakdown:
    """Costo para un preset day/week/month/year (`EnergySummary` ya trae
    period_start/period_end/total_kwh/series resueltos)."""
    return compute_cost_from_points(
        config,
        period,
        consumption.period_start,
        consumption.period_end,
        device_id,
        consumption.series,
        export.series,
        consumption.total_kwh,
        export.total_kwh,
    )
