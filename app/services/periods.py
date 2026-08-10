"""Resolución unificada de periodos (día/semana/mes/año) y rangos libres.

Antes la lógica de "qué rango corresponde a X y con qué intervalo se agrega"
estaba reimplementada en analytics, kpis, energy/summary, reports/builder y
dashboard. Este módulo es el único lugar que decide eso; los consuming
modulos solo piden un `PeriodBounds` y usan `start`/`stop`/`interval`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.services.analytics.common import auto_interval
from app.utils.period import start_of_day, start_of_month, start_of_week, start_of_year

PeriodKind = Literal["day", "week", "month", "year"]

# Vocabulario de /reports (daily...) → PeriodKind.
_REPORT_ALIASES: dict[str, PeriodKind] = {
    "daily": "day",
    "weekly": "week",
    "monthly": "month",
    "yearly": "year",
}

# Intervalos de agregación por periodo (mismos valores que usaba cada
# modulo antes de la unificación: día=1h, semana/mes/año=1d).
_INTERVALS: dict[PeriodKind, timedelta] = {
    "day": timedelta(hours=1),
    "week": timedelta(days=1),
    "month": timedelta(days=1),
    "year": timedelta(days=1),
}


@dataclass(frozen=True)
class PeriodBounds:
    start: datetime
    stop: datetime
    interval: timedelta

    def __post_init__(self) -> None:
        if self.start >= self.stop:
            raise ValueError("'from' debe ser anterior a 'to'")


def _start_of(kind: PeriodKind, tz_name: str, now: datetime) -> datetime:
    return {
        "day": start_of_day,
        "week": start_of_week,
        "month": start_of_month,
        "year": start_of_year,
    }[kind](tz_name, now)


def resolve_period(
    kind: str,
    tz_name: str,
    now: datetime | None = None,
    *,
    from_: datetime | None = None,
    to: datetime | None = None,
) -> PeriodBounds:
    """Límites + intervalo de agregación de un periodo.

    Acepta los vocabularios day/week/month/year y daily/weekly/monthly/
    yearly, además de "custom".

    - Presets fijos: `start` = inicio del periodo alineado a `tz_name`
      (medianoche local, lunes ISO, día 1, 1 de enero); `from_`/`to`
      explícitos sobrescriben el inicio/fin.
    - "custom" exige `from_` y `to`, y el intervalo sale de `auto_interval()`
      (~500 puntos en el rango).

    Lanza ValueError si el rango resultante es inválido (`start >= stop`).
    """
    reference = now or datetime.now(tz=UTC)

    if kind == "custom":
        if from_ is None or to is None:
            raise ValueError("'custom' requiere from_ y to")
        return PeriodBounds(from_, to, auto_interval(from_, to))

    normalized: PeriodKind = _REPORT_ALIASES.get(kind, kind)  # type: ignore[assignment]
    start = from_ if from_ is not None else _start_of(normalized, tz_name, reference)
    stop = to if to is not None else reference
    return PeriodBounds(start, stop, _INTERVALS[normalized])
