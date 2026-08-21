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

#: Cada cuánto se agrupan las barras de energía, cuando el cliente lo elige.
EnergyBucket = Literal["hour", "day", "week"]

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

_HORA = timedelta(hours=1)
_DIA = timedelta(days=1)
_SEMANA = timedelta(days=7)


_BUCKETS: dict[str, timedelta] = {"hour": _HORA, "day": _DIA, "week": _SEMANA}


def energy_bucket(
    start: datetime, stop: datetime, elegido: EnergyBucket | None = None
) -> timedelta:
    """Cada cuánto se agrupan las BARRAS de energía de un rango libre.

    No es lo mismo que el intervalo de lectura. La potencia se lee fina —si no,
    un pico de tres minutos se pierde dentro del promedio de su ventana—, pero
    las barras las mira una persona: con `auto_interval` un rango de 30 días
    salía en ventanas de ~86 minutos, o sea ~500 barras de dos píxeles donde se
    esperaba "los kWh de cada día".

    La escalera es la misma que usan los periodos fijos, para que un rango
    libre de un mes se vea igual que la pestaña Mensual:

        < 48 h    → una hora   (como Diario)
        < 400 d   → un día     (como Semanal / Mensual)
        resto     → una semana (para que un rango de años siga siendo legible)

    `elegido` la sobrescribe: el cliente puede pedir más detalle del que la
    escalera propone —"quiero ver hora por hora estos 30 días"— y el default
    solo decide qué se muestra cuando nadie eligió nada.
    """
    if elegido is not None:
        return _BUCKETS[elegido]

    span = stop - start
    if span < timedelta(hours=48):
        return _HORA
    if span < timedelta(days=400):
        return _DIA
    return _SEMANA


@dataclass(frozen=True)
class PeriodBounds:
    start: datetime
    stop: datetime
    #: Ventana de LECTURA: la resolución a la que se piden las series de
    #: potencia. Fina a propósito — de ella depende que la demanda pico no se
    #: promedie hasta desaparecer.
    interval: timedelta
    #: Ventana de las BARRAS de energía (y de los costos, que salen de ellas).
    #: La mira una persona, así que se mide en horas o días, no en "500 puntos".
    energy_interval: timedelta

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
    bucket: EnergyBucket | None = None,
) -> PeriodBounds:
    """Límites + intervalo de agregación de un periodo.

    Acepta los vocabularios day/week/month/year y daily/weekly/monthly/
    yearly, además de "custom".

    - Presets fijos: `start` = inicio del periodo alineado a `tz_name`
      (medianoche local, lunes ISO, día 1, 1 de enero); `from_`/`to`
      explícitos sobrescriben el inicio/fin.
    - "custom" exige `from_` y `to`. El intervalo de lectura sale de
      `auto_interval()` (~500 puntos), y el de las barras de `energy_bucket()`
      —una escalera en horas/días, que es lo que se puede leer en pantalla—.

    Lanza ValueError si el rango resultante es inválido (`start >= stop`).
    """
    reference = now or datetime.now(tz=UTC)

    if kind == "custom":
        if from_ is None or to is None:
            raise ValueError("'custom' requiere from_ y to")
        return PeriodBounds(from_, to, auto_interval(from_, to), energy_bucket(from_, to, bucket))

    normalized: PeriodKind = _REPORT_ALIASES.get(kind, kind)  # type: ignore[assignment]
    start = from_ if from_ is not None else _start_of(normalized, tz_name, reference)
    stop = to if to is not None else reference
    # En los periodos fijos las dos ventanas coinciden salvo que el cliente
    # pida otra cosa: su intervalo ya está elegido para leerse (1 h el día,
    # 1 d el resto).
    barras = _BUCKETS[bucket] if bucket is not None else _INTERVALS[normalized]
    return PeriodBounds(start, stop, _INTERVALS[normalized], barras)
