"""Resumen general de analítica: consumo/exportación por periodo + patrón
horario típico + recomendación de eficiencia costo/energía.

Nada de esto es un dato nuevo — es una relectura de KPIs (`compute_kpis`),
el perfil horario (`daily_profile`) y la tarifa configurada (inyectada
por quien llama, ver `app/dependencies/tariff.py`), pensada para un
reporte tipo "resumen general" que la página de Analítica exporta (PDF
del lado del frontend).
"""

import asyncio
from datetime import UTC, datetime

from app.core.config import Settings
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import AnalyticsSummary, HourProfilePoint
from app.schemas.tariff import EfficiencyRecommendation, TariffConfig
from app.services.analytics.profile import daily_profile
from app.services.kpis.summary import compute_kpis
from app.services.tariff.cost import rate_for_month


def _month_key(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _peak_hours(profile: list[HourProfilePoint]) -> tuple[int | None, int | None]:
    """(hora de mayor importación típica, hora de mayor exportación típica).
    None si el perfil no tiene ninguna hora con ese signo."""
    importing = [p for p in profile if p.power_avg_w > 0]
    exporting = [p for p in profile if p.power_avg_w < 0]
    peak_consumption_hour = max(importing, key=lambda p: p.power_avg_w).hour if importing else None
    peak_export_hour = min(exporting, key=lambda p: p.power_avg_w).hour if exporting else None
    return peak_consumption_hour, peak_export_hour


def _efficiency_recommendation(
    config: TariffConfig, export_monthly_kwh: float, consumption_monthly_kwh: float
) -> EfficiencyRecommendation | None:
    """None si el mes en curso no tiene NINGUNA tarifa registrada todavía
    (ni siquiera una anterior de la cual estimar) — no se inventa un
    ahorro sin tarifa de referencia.

    El tramo 1 del excedente (hasta lo importado en el mes) ya se paga al
    mismo precio que importar — ahí no hay nada que ganar autoconsumiendo.
    Solo el tramo 2 (lo exportado por encima de lo importado) tiene el gap
    `cu_cop_kwh - excedente_cop_kwh` por kWh (ver
    `app/services/tariff/cost.py`).
    """
    month = _month_key(datetime.now(tz=UTC))
    rate, stale = rate_for_month(config, month)
    if rate is None:
        return None
    tier2_kwh = max(0.0, export_monthly_kwh - consumption_monthly_kwh)
    delta_cop_kwh = rate.cu_cop_kwh - rate.excedente_cop_kwh
    return EfficiencyRecommendation(
        tariff_month=rate.month,
        stale=stale,
        cu_cop_kwh=rate.cu_cop_kwh,
        excedente_cop_kwh=rate.excedente_cop_kwh,
        export_kwh=export_monthly_kwh,
        potential_savings_cop=round(tier2_kwh * delta_cop_kwh, 2),
    )


async def analytics_summary(
    repo: InfluxDataSource,
    settings: Settings,
    start: datetime,
    stop: datetime,
    device_id: str | None,
    tariff: TariffConfig,
) -> AnalyticsSummary:
    kpis, profile = await asyncio.gather(
        compute_kpis(repo, settings, start, stop, device_id),
        daily_profile(repo, start, stop, device_id, settings.TIMEZONE),
    )
    peak_consumption_hour, peak_export_hour = _peak_hours(profile)
    efficiency = _efficiency_recommendation(
        tariff, kpis.export_monthly_kwh, kpis.consumption_monthly_kwh
    )

    return AnalyticsSummary(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        consumption_daily_kwh=kpis.consumption_daily_kwh,
        consumption_weekly_kwh=kpis.consumption_weekly_kwh,
        consumption_monthly_kwh=kpis.consumption_monthly_kwh,
        export_daily_kwh=kpis.export_daily_kwh,
        export_monthly_kwh=kpis.export_monthly_kwh,
        hourly_profile=profile,
        peak_consumption_hour=peak_consumption_hour,
        peak_export_hour=peak_export_hour,
        efficiency=efficiency,
    )
