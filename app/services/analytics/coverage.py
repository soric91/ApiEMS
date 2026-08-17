"""Cuánto dato hay realmente en un rango.

Un hueco de datos no es consumo cero, pero se ve igual: si el gateway estuvo
caído diez horas, el día aparece "bajo" y nadie se entera. Todo lo que compara
periodos entre sí (mes contra mes, año contra año, un sitio contra otros)
depende de saber que los dos lados están completos.

La cobertura de una ventana es `muestras recibidas / muestras esperadas`. Las
esperadas salen del intervalo de lectura que el gateway tiene configurado en el
CRM. Cuando no se conoce, se infiere del propio rango: el percentil 90 de las
muestras por ventana es lo que el equipo consigue cuando todo va bien, y sirve
de referencia sin inventar un número fijo.
"""

import asyncio
from datetime import datetime, timedelta

import polars as pl

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import CoveragePoint, CoverageResult
from app.schemas.influx import TimeSeriesPoint
from app.services.analytics.common import series_quantile

# La variable con la que se mide la cobertura. La potencia activa total la
# reporta cualquier medidor de la flota en cada lectura, así que sus muestras
# son el pulso del equipo. Medir sobre una variable que este medidor no tiene
# daría 0% de cobertura con los datos perfectos.
REFERENCE_VARIABLE = Variable.POWER_ACTIVE_INST_TOTAL

# Por debajo de esto la ventana se marca como incompleta en el panel.
INCOMPLETE_BELOW = 0.8

# Cuánto se apoya la inferencia: el percentil 90 es "lo que el equipo consigue
# cuando todo va bien". La mediana mentiría en un rango con la mitad de los
# datos perdidos, y el máximo se dispararía con una ventana que por redondeo
# recibió una muestra de más.
_REFERENCE_QUANTILE = 0.90


def _expected_from_counts(counts: list[TimeSeriesPoint]) -> float | None:
    """Muestras por ventana de referencia, inferidas del propio rango."""
    if not counts:
        return None
    series = pl.Series([point.value for point in counts])
    expected = series_quantile(series, _REFERENCE_QUANTILE)
    return expected if expected is not None and expected > 0 else None


def _points(counts: list[TimeSeriesPoint], expected: float) -> list[CoveragePoint]:
    return [
        CoveragePoint(
            time=point.time,
            samples=int(point.value),
            # Acotado a 1: una ventana puede recibir una muestra de más por
            # redondeo del reloj del equipo, y un 104% de cobertura no
            # significa nada.
            ratio=round(min(1.0, point.value / expected), 4),
        )
        for point in counts
    ]


async def coverage(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    every: timedelta,
    device_id: str | None,
    interval_seconds: int | None,
) -> CoverageResult:
    """Cobertura por ventana en el rango.

    `interval_seconds` es el intervalo de lectura declarado en el CRM para el
    gateway. Sin él, se infiere del rango y se marca como tal: la cifra sigue
    sirviendo para ver DÓNDE están los huecos, que es a lo que se mira, aunque
    el 100% sea relativo al mejor tramo en vez de a un valor configurado.
    """
    counts = await repo.sample_counts(REFERENCE_VARIABLE, start, stop, every, device_id)

    bucket_seconds = int(every.total_seconds())
    if interval_seconds is not None and interval_seconds > 0:
        expected = bucket_seconds / interval_seconds
        source = "declarado"
    else:
        expected = await asyncio.to_thread(_expected_from_counts, counts)
        source = "inferido" if expected is not None else "desconocido"

    if expected is None:
        return CoverageResult(
            device_id=device_id,
            period_start=start,
            period_end=stop,
            bucket_seconds=bucket_seconds,
            expected_per_bucket=None,
            expected_source="desconocido",
            overall_ratio=None,
            incomplete_buckets=0,
            points=[],
        )

    points = _points(counts, expected)
    total_esperado = expected * len(counts)
    overall = (
        round(min(1.0, sum(point.samples for point in points) / total_esperado), 4)
        if total_esperado > 0
        else None
    )
    return CoverageResult(
        device_id=device_id,
        period_start=start,
        period_end=stop,
        bucket_seconds=bucket_seconds,
        expected_per_bucket=round(expected, 2),
        expected_source=source,
        overall_ratio=overall,
        incomplete_buckets=sum(1 for point in points if point.ratio < INCOMPLETE_BELOW),
        points=points,
    )
