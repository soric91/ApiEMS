from datetime import UTC, datetime, timedelta

from app.models.variables import Variable
from app.schemas.alerts import BandStats
from app.schemas.influx import EnergyPoint, TimeSeriesPoint
from app.services.analytics.anomaly import (
    classify,
    hourly_power_baseline,
    weekday_total_baseline,
)
from tests.fakes import FakeInfluxRepository

TZ = "UTC"  # evita complejidad de offset en los tests; local_hour == hora UTC


def _hourly_points(hour: int, values: list[float], day_start: int = 1) -> list[TimeSeriesPoint]:
    return [
        TimeSeriesPoint(time=datetime(2026, 4, day_start + i, hour, tzinfo=UTC), value=v)
        for i, v in enumerate(values)
    ]


async def test_hourly_power_baseline_skips_buckets_below_min_samples() -> None:
    repo = FakeInfluxRepository()
    repo.instant_series_by_variable = {
        Variable.POWER_ACTIVE_INST_TOTAL: (
            _hourly_points(10, [float(v) for v in range(90, 115)])  # 25 muestras: pasa
            + _hourly_points(11, [100.0, 105.0])  # 2 muestras: no alcanza MIN_SAMPLES
        )
    }
    bands = await hourly_power_baseline(repo, "11", TZ)
    assert 10 in bands
    assert 11 not in bands
    assert bands[10].p10 < bands[10].p90
    assert bands[10].sample_count == 25


async def test_hourly_power_baseline_empty_series() -> None:
    repo = FakeInfluxRepository()
    assert await hourly_power_baseline(repo, "11", TZ) == {}


async def test_weekday_total_baseline_skips_below_min_samples() -> None:
    repo = FakeInfluxRepository()
    # 4 lunes (weekday=0) + 1 martes (weekday=1, insuficiente)
    mondays = [datetime(2026, 4, 6, tzinfo=UTC) + timedelta(weeks=i) for i in range(4)]
    repo.energy_series_points_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [
            EnergyPoint(time=t, value=10.0 + i) for i, t in enumerate(mondays)
        ]
        + [EnergyPoint(time=datetime(2026, 4, 7, tzinfo=UTC), value=8.0)]
    }
    bands = await weekday_total_baseline(repo, "11", TZ)
    assert 0 in bands  # lunes
    assert 1 not in bands  # martes


async def test_weekday_total_baseline_empty() -> None:
    repo = FakeInfluxRepository()
    assert await weekday_total_baseline(repo, "11", TZ) == {}


def test_classify_within_band_is_none() -> None:
    band = BandStats(p10=100.0, p90=200.0, sample_count=30)
    assert classify(150.0, band) is None
    assert classify(100.0, band) is None
    assert classify(200.0, band) is None


def test_classify_moderate() -> None:
    band = BandStats(p10=100.0, p90=200.0, sample_count=30)  # width=100
    assert classify(220.0, band) == "moderate"  # 20 de distancia <= 50
    assert classify(60.0, band) == "moderate"  # 40 de distancia <= 50


def test_classify_high() -> None:
    band = BandStats(p10=100.0, p90=200.0, sample_count=30)
    assert classify(300.0, band) == "high"  # 100 de distancia > 50
    assert classify(10.0, band) == "high"


def test_classify_degenerate_band_never_alerts() -> None:
    band = BandStats(p10=100.0, p90=100.0, sample_count=30)
    assert classify(500.0, band) is None
