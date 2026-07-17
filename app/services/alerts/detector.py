"""Orquesta la detección: baseline + clasificación + construcción del Alert."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.alerts import Alert
from app.schemas.mqtt import DeviceReading
from app.services.analytics.anomaly import (
    classify,
    hourly_power_baseline,
    weekday_total_baseline,
)
from app.utils.period import local_hour, start_of_day

_WEEKDAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


async def check_hourly(
    repo: InfluxDataSource, reading: DeviceReading, settings: Settings
) -> Alert | None:
    """Evalúa una lectura MQTT contra la banda horaria.

    Regla de dominio: exportar excedente (POWER_ACTIVE_INST_TOTAL <= 0) es
    SIEMPRE favorable — nunca genera alerta, sin importar cuánto se aleje de
    lo típico (exportar más de lo normal no es un problema). Solo se evalúa
    cuando el valor es positivo (importando):

    - Si a esa hora históricamente casi siempre se exporta (`band.p90 < 0`,
      es decir el 90% de las muestras históricas fueron negativas), importar
      ahora es la señal fuerte de que algo anda mal — el sistema solar pudo
      apagarse o el consumo superó a la generación — se marca `high` directo,
      sin depender de la magnitud.
    - Si la hora normalmente admite importación (de noche, sin sol), se
      compara con la banda estadística de siempre (`classify`).
    """
    value = reading.data.get(Variable.POWER_ACTIVE_INST_TOTAL.value)
    if value is None or value <= 0:
        return None

    device_id = str(reading.device_id)
    bands = await hourly_power_baseline(repo, device_id, settings.TIMEZONE)
    hour = local_hour(reading.timestamp, settings.TIMEZONE)
    band = bands.get(hour)
    if band is None:
        return None

    sign_flip = band.p90 < 0
    severity = "high" if sign_flip else classify(value, band)
    if severity is None:
        return None

    if sign_flip:
        message = (
            f"Se esperaba estar exportando excedente solar a las {hour:02d}:00 "
            f"(históricamente esta hora exporta) pero se está importando "
            f"{value:.0f} W de la red — posible falla del sistema solar o "
            "consumo elevado."
        )
    else:
        message = (
            f"Potencia importada inusual a las {hour:02d}:00 "
            f"({value:.0f} W; lo típico para esta hora es entre "
            f"{band.p10:.0f} y {band.p90:.0f} W)"
        )

    return Alert(
        kind="hourly_power",
        severity=severity,
        device_id=device_id,
        variable=Variable.POWER_ACTIVE_INST_TOTAL.value,
        value=value,
        expected_low=band.p10,
        expected_high=band.p90,
        bucket=hour,
        timestamp=reading.timestamp,
        message=message,
    )


async def check_daily_total(
    repo: InfluxDataSource,
    settings: Settings,
    device_id: str | None,
    now: datetime | None = None,
) -> Alert | None:
    """Compara el ÚLTIMO DÍA COMPLETO (ayer, en TIMEZONE) contra la banda de
    su día de semana. No evalúa el día en curso: un total parcial siempre
    parecería "bajo" frente a un día completo, dando falsos positivos."""
    today_start = start_of_day(settings.TIMEZONE, now)
    yesterday_start = today_start - timedelta(days=1)

    bands = await weekday_total_baseline(repo, device_id, settings.TIMEZONE)
    weekday = _weekday_of(yesterday_start, settings.TIMEZONE)
    band = bands.get(weekday)
    if band is None:
        return None

    total = await repo.energy_total(
        Variable.POWER_ACTIVE_TOTAL_POS, yesterday_start, today_start, device_id
    )
    severity = classify(total, band)
    if severity is None:
        return None

    return Alert(
        kind="daily_total",
        severity=severity,
        device_id=device_id,
        variable=Variable.POWER_ACTIVE_TOTAL_POS.value,
        value=total,
        expected_low=band.p10,
        expected_high=band.p90,
        bucket=weekday,
        timestamp=yesterday_start,
        message=(
            f"Consumo del {_WEEKDAY_NAMES[weekday]} inusual: {total:.2f} kWh "
            f"(lo típico es entre {band.p10:.2f} y {band.p90:.2f} kWh)"
        ),
    )


def _weekday_of(dt: datetime, tz_name: str) -> int:
    return dt.astimezone(ZoneInfo(tz_name)).weekday()
