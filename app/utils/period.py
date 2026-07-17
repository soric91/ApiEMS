"""Límites de periodo (día/semana/mes/año) en la zona horaria configurada.

Los timestamps que expone la API son siempre UTC; estos límites solo se
usan para decidir qué rango de datos corresponde a "hoy", "esta semana",
etc. en la zona horaria real de la residencia (TIMEZONE en .env).
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def _now_local(tz_name: str, now: datetime | None) -> datetime:
    reference = now or datetime.now(tz=UTC)
    return reference.astimezone(ZoneInfo(tz_name))


def start_of_day(tz_name: str, now: datetime | None = None) -> datetime:
    local = _now_local(tz_name, now)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def start_of_week(tz_name: str, now: datetime | None = None) -> datetime:
    """Semana ISO: inicia el lunes."""
    local = _now_local(tz_name, now)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=local.weekday()
    )
    return start.astimezone(UTC)


def start_of_month(tz_name: str, now: datetime | None = None) -> datetime:
    local = _now_local(tz_name, now)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def start_of_year(tz_name: str, now: datetime | None = None) -> datetime:
    local = _now_local(tz_name, now)
    start = local.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def local_hour(dt: datetime, tz_name: str) -> int:
    """Hora local (0-23) de un timestamp UTC, en la zona configurada."""
    return dt.astimezone(ZoneInfo(tz_name)).hour


def flux_window_offset(tz_name: str, at: datetime | None = None) -> timedelta:
    """Desplazamiento para alinear `aggregateWindow()` de InfluxDB (que por
    defecto usa límites UTC) a los límites de día/semana en `tz_name`.

    Ej. America/Bogota es UTC-5 todo el año (sin horario de verano): la
    medianoche local cae a las 05:00 UTC, así que una ventana diaria sin
    offset se "come" 5 horas del día anterior en hora local. Con
    `offset=5h`, `aggregateWindow` alinea sus ventanas a la medianoche real.

    Es un no-op seguro para ventanas que dividen exacto el offset (ej. 1h,
    con Bogotá: 5h % 1h == 0 → mismos límites de siempre) y corrige las de
    1 día o más. Se calcula dinámicamente por si `TIMEZONE` cambia — no
    asumir un offset fijo.
    """
    reference = at or datetime.now(tz=UTC)
    local = reference.astimezone(ZoneInfo(tz_name))
    utc_offset = local.utcoffset() or timedelta(0)
    return (-utc_offset) % timedelta(days=1)


def month_starts_of_year(tz_name: str, now: datetime | None = None) -> list[datetime]:
    """Inicio (UTC) de cada mes calendario del año en curso, de enero al mes actual."""
    local = _now_local(tz_name, now)
    tz = ZoneInfo(tz_name)
    return [
        datetime(local.year, month, 1, tzinfo=tz).astimezone(UTC)
        for month in range(1, local.month + 1)
    ]
