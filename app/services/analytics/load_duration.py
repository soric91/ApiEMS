"""Curva de duración de carga: cuánto tiempo se está por encima de cada nivel.

Es la vista clásica de análisis energético industrial y la que contesta "¿mi
consumo es parejo o vive de picos?". Se ordena la potencia importada de mayor
a menor y se grafica contra el porcentaje del tiempo: si la curva cae en
picada, unas pocas horas explican la mayor parte de la energía; si es plana,
el consumo es de fondo.

Dos números salen de ahí y son los que se leen:

- Los percentiles (p1, p5, p50, p95): "el 5% del tiempo estás por encima de
  4,2 kW".
- La concentración: qué porción de la ENERGÍA se consume en ese 5% del tiempo
  más alto. Es lo que dice si vale la pena atacar los picos o el fondo.

Solo importación: durante la exportación no hay demanda que la red esté
sirviendo, y meter valores negativos en una curva de carga la vuelve ilegible
(la misma regla que ya siguen demanda máxima, factor de carga y carga base).
"""

import asyncio
from datetime import datetime, timedelta

import polars as pl

from app.models.variables import Aggregation, Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import LoadDurationPoint, LoadDurationResult
from app.schemas.influx import TimeSeriesPoint
from app.services.influx.cache import cached_instant_series

# Resolución del muestreo. Quince minutos es el estándar de facturación de
# demanda y da 2.880 muestras al mes: suficiente para la forma de la curva sin
# traer el crudo de 1 Hz.
SAMPLE = timedelta(minutes=15)
# Cuántos puntos se devuelven para dibujar. Doscientos alcanzan para una curva
# suave; mandar las 2.880 muestras sería mandar la serie entera con otro orden.
DEFAULT_POINTS = 200
# Sobre qué fracción del tiempo se mide la concentración de energía.
TOP_FRACTION = 0.05


def _curva(
    points: list[TimeSeriesPoint], salida: int
) -> tuple[list[LoadDurationPoint], list[float]]:
    """(curva muestreada, valores ordenados de mayor a menor) — cálculo puro."""
    series = pl.Series([p.value for p in points]).filter(  # pyright: ignore[reportUnknownMemberType]
        pl.Series([p.value > 0 for p in points])
    )
    if series.is_empty():
        return [], []
    ordenados: list[float] = sorted(series.to_list(), reverse=True)  # pyright: ignore[reportUnknownMemberType]

    total = len(ordenados)
    curva: list[LoadDurationPoint] = []
    for i in range(min(salida, total)):
        fraccion = i / (salida - 1) if salida > 1 else 0.0
        indice = min(total - 1, round(fraccion * (total - 1)))
        curva.append(
            LoadDurationPoint(time_fraction=round(fraccion, 4), power_w=round(ordenados[indice], 2))
        )
    return curva, ordenados


def _percentil_superior(ordenados: list[float], fraccion: float) -> float:
    """La potencia que se supera durante `fraccion` del tiempo.

    El índice se trunca (`int`), no se redondea: con 100 muestras, el 5% del
    tiempo son exactamente las 5 más altas, así que el umbral es la sexta. Con
    redondeo, p95 caía una muestra corrido y la lectura "el 95% del tiempo
    estás por encima de X" dejaba de ser cierta en los extremos.
    """
    indice = min(len(ordenados) - 1, int(fraccion * len(ordenados)))
    return round(ordenados[indice], 2)


async def load_duration(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
    salida: int = DEFAULT_POINTS,
) -> LoadDurationResult:
    """La curva de duración de carga del rango, con sus percentiles."""
    points = await cached_instant_series(
        repo, Variable.POWER_ACTIVE_INST_TOTAL, start, stop, SAMPLE, Aggregation.MEAN, device_id
    )
    vacia = LoadDurationResult(
        device_id=device_id,
        period_start=start,
        period_end=stop,
        sample_seconds=int(SAMPLE.total_seconds()),
        points=[],
        p1_w=None,
        p5_w=None,
        p50_w=None,
        p95_w=None,
        top_fraction=TOP_FRACTION,
        top_energy_share=None,
        sample_count=0,
    )
    if not points:
        return vacia

    curva, ordenados = await asyncio.to_thread(_curva, points, salida)
    if not ordenados:
        return vacia

    total_energia = sum(ordenados)
    cuantas_del_top = max(1, round(len(ordenados) * TOP_FRACTION))
    top_share = (
        round(sum(ordenados[:cuantas_del_top]) / total_energia, 4) if total_energia > 0 else None
    )

    return LoadDurationResult(
        device_id=device_id,
        period_start=start,
        period_end=stop,
        sample_seconds=int(SAMPLE.total_seconds()),
        points=curva,
        p1_w=_percentil_superior(ordenados, 0.01),
        p5_w=_percentil_superior(ordenados, 0.05),
        p50_w=_percentil_superior(ordenados, 0.50),
        p95_w=_percentil_superior(ordenados, 0.95),
        top_fraction=TOP_FRACTION,
        top_energy_share=top_share,
        sample_count=len(ordenados),
    )
