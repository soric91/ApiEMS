"""Baseline estadístico para detección de anomalías de consumo, sin ML.

Dos bandas independientes, cada una con su propia granularidad para que la
muestra por bucket sea estadísticamente razonable con ~90 días de datos:

- Horaria: POWER_ACTIVE_INST_TOTAL agrupado SOLO por hora local (0-23),
  agrupando todos los días de la semana entre sí (~90 muestras/bucket con
  90 días de historia). Sirve para "¿esta lectura es rara para esta hora
  del día?", evaluado en cada mensaje MQTT.
- Diaria por día de semana: total de energía importada por día, agrupado
  por día de semana (~13 muestras/bucket con 90 días). Sirve para "¿el
  último día completo consumió más/menos de lo típico para ese día de la
  semana?".

Clasificación tipo cerca de Tukey (tal como un boxplot): la banda [p10, p90]
es "normal"; más allá, hasta medio ancho de banda extra, es "moderate"; más
allá de eso, "high". No hay entrenamiento ni modelo — son percentiles reales
sobre datos reales, recalculados cada 24h (cache TTL).
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl

from app.core.cache import cached
from app.models.variables import Aggregation, Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.alerts import AlertSeverity, BandStats
from app.services.analytics.common import series_quantile
from app.utils.period import local_hour, start_of_day

MIN_SAMPLES = 20
MIN_WEEKDAY_SAMPLES = 3  # solo ~13 muestras posibles por día de semana en 90 días
BASELINE_TTL = 86400  # 24h: la banda no cambia de forma relevante intradía


@cached(ttl_seconds=BASELINE_TTL)
async def hourly_power_baseline(
    repo: InfluxDataSource,
    device_id: str | None,
    tz_name: str,
    days: int = 30,
) -> dict[int, BandStats]:
    """Banda [p10, p90] de POWER_ACTIVE_INST_TOTAL por hora local (0-23),
    agrupando todos los días de la semana."""
    stop = datetime.now(tz=UTC)
    start = stop - timedelta(days=days)
    points = await repo.instant_series(
        Variable.POWER_ACTIVE_INST_TOTAL,
        start,
        stop,
        timedelta(hours=1),
        Aggregation.MEAN,
        device_id,
    )
    if not points:
        return {}

    df = pl.DataFrame(
        {
            "hour": [local_hour(p.time, tz_name) for p in points],
            "value": [p.value for p in points],
        }
    )
    bands: dict[int, BandStats] = {}
    for hour in range(24):
        series: pl.Series = df.filter(pl.col("hour") == hour)[  # pyright: ignore[reportUnknownMemberType]
            "value"
        ]
        if len(series) < MIN_SAMPLES:
            continue
        p10 = series_quantile(series, 0.10)
        p90 = series_quantile(series, 0.90)
        if p10 is None or p90 is None:
            continue
        bands[hour] = BandStats(p10=p10, p90=p90, sample_count=len(series))
    return bands


@cached(ttl_seconds=BASELINE_TTL)
async def weekday_total_baseline(
    repo: InfluxDataSource,
    device_id: str | None,
    tz_name: str,
    days: int = 30,
) -> dict[int, BandStats]:
    """Banda [p10, p90] de energía importada diaria (kWh) por día de semana
    (0=lunes..6=domingo), sobre los últimos `days` días COMPLETOS."""
    today_start = start_of_day(tz_name)
    start = today_start - timedelta(days=days)
    daily_points = await repo.energy_series(
        Variable.POWER_ACTIVE_TOTAL_POS, start, today_start, timedelta(days=1), device_id
    )
    if not daily_points:
        return {}

    tz = ZoneInfo(tz_name)
    df = pl.DataFrame(
        {
            "weekday": [p.time.astimezone(tz).weekday() for p in daily_points],
            "value": [p.value for p in daily_points],
        }
    )
    bands: dict[int, BandStats] = {}
    for weekday in range(7):
        series: pl.Series = df.filter(pl.col("weekday") == weekday)[  # pyright: ignore[reportUnknownMemberType]
            "value"
        ]
        if len(series) < MIN_WEEKDAY_SAMPLES:
            continue
        p10 = series_quantile(series, 0.10)
        p90 = series_quantile(series, 0.90)
        if p10 is None or p90 is None:
            continue
        bands[weekday] = BandStats(p10=p10, p90=p90, sample_count=len(series))
    return bands


def classify(value: float, band: BandStats) -> AlertSeverity | None:
    """None = dentro de lo normal. 'moderate'/'high' según qué tan lejos de
    la banda [p10, p90] cae el valor, en unidades del propio ancho de banda."""
    width = band.p90 - band.p10
    if width <= 0:
        return None  # banda degenerada (todas las muestras iguales): no alertar
    if band.p10 <= value <= band.p90:
        return None
    distance = band.p10 - value if value < band.p10 else value - band.p90
    if distance <= width * 0.5:
        return "moderate"
    return "high"
