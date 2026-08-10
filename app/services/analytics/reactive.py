"""Cuadrantes de energía reactiva (kvarh) de un período.

Regla de dominio central, la misma que para la energía activa: los cuatro
cuadrantes son contadores acumulativos monótonos crecientes — jamás admiten
mean/max/min, solo `difference()` para la energía del período (fluía en
`energy_total` como spread()) y `last()` para el valor puntual.
"""

import asyncio
from datetime import datetime, timedelta

from app.models.variables import REACTIVE_QUADRANTS
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import ReactiveQuadrantPoint, ReactiveQuadrantsResult
from app.services.influx.cache import cached_energy_series, cached_energy_total

# Claves del payload en el mismo orden que REACTIVE_QUADRANTS.
QUADRANT_KEYS: tuple[str, ...] = ("q1_kvarh", "q2_kvarh", "q3_kvarh", "q4_kvarh")


def _bucket(span: timedelta) -> timedelta:
    """Ancho de ventana de la tendencia según cuánto abarca el período.

    Estable y deliberadamente grueso (mínimo 1 hora): reescalar los ejes con
    cada carga distrae, y la reactiva se mueve en escalas de horas, no de
    minutos.
    """
    if span <= timedelta(days=1):
        return timedelta(hours=1)
    if span <= timedelta(days=7):
        return timedelta(hours=3)
    if span <= timedelta(days=62):
        return timedelta(hours=6)
    return timedelta(days=1)


async def reactive_quadrants(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
) -> ReactiveQuadrantsResult:
    every = _bucket(stop - start)
    totals, series = await asyncio.gather(
        asyncio.gather(
            *(cached_energy_total(repo, v, start, stop, device_id) for v in REACTIVE_QUADRANTS)
        ),
        asyncio.gather(
            *(
                cached_energy_series(repo, v, start, stop, every, device_id)
                for v in REACTIVE_QUADRANTS
            )
        ),
    )

    q = [round(float(t), 2) for t in totals]
    total_import = q[0] + q[1]
    total_export = q[2] + q[3]
    balance = total_import - total_export

    dominant_index = max(range(4), key=lambda i: q[i]) if any(q) else None
    dominant = f"q{dominant_index + 1}" if dominant_index is not None else None
    dominant_kvarh = q[dominant_index] if dominant_index is not None else 0.0

    # Todas las ventanas usan el mismo `every` y el mismo offset (dependen de la
    # zona horaria y del inicio), así que los `time` de las cuatro series
    # coinciden uno a uno y se alinean por instante exacto.
    by_time: dict[datetime, list[float]] = {}
    for index, points in enumerate(series):
        for point in points:
            by_time.setdefault(point.time, [0.0] * 4)[index] = point.value

    trend = [
        ReactiveQuadrantPoint(time=t, **dict(zip(QUADRANT_KEYS, values, strict=True)))
        for t, values in sorted(by_time.items())
    ]

    return ReactiveQuadrantsResult(
        period_start=start,
        period_end=stop,
        device_id=device_id,
        q1_kvarh=q[0],
        q2_kvarh=q[1],
        q3_kvarh=q[2],
        q4_kvarh=q[3],
        total_import_kvarh=total_import,
        total_export_kvarh=total_export,
        balance_kvarh=round(balance, 2),
        dominant=dominant,
        dominant_kvarh=dominant_kvarh,
        trend=trend,
    )
