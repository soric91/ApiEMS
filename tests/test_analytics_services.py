from datetime import UTC, datetime, timedelta

from app.models.variables import Variable
from app.schemas.influx import EnergyPoint, TimeSeriesPoint
from app.services.analytics.common import (
    DEFAULT_PERCENTILE,
    base_load_result,
    load_factor_result,
    max_demand_result,
    power_frames,
)
from app.services.analytics.compare import compare_periods
from app.services.analytics.profile import daily_profile, weekday_profile
from tests.fakes import FakeInfluxRepository

START = datetime(2026, 7, 13, tzinfo=UTC)  # lunes
STOP = datetime(2026, 7, 16, tzinfo=UTC)  # jueves


def _power_points(values: list[float], start: datetime = START) -> list[TimeSeriesPoint]:
    return [TimeSeriesPoint(time=start + timedelta(hours=i), value=v) for i, v in enumerate(values)]


def _importing(values: list[float]):
    """El marco de solo-importación desde una lista de potencias netas.

    Los indicadores de carga son cálculo puro sobre este marco; quien lo lee de
    InfluxDB es `reports.builder.consolidate`, que tiene sus propios tests.
    """
    _, importing = power_frames(_power_points(values))
    return importing


def test_max_demand_only_considers_importing_samples() -> None:
    result = max_demand_result(START, STOP, None, _importing([-500.0, 300.0, -100.0, 900.0, 200.0]))
    assert result.peak_power_w == 900.0
    assert result.peak_at == START + timedelta(hours=3)


def test_max_demand_none_when_always_exporting() -> None:
    result = max_demand_result(START, STOP, None, _importing([-500.0, -300.0, -100.0]))
    assert result.peak_power_w is None
    assert result.peak_at is None


def test_max_demand_empty_series() -> None:
    _, importing = power_frames([])
    result = max_demand_result(START, STOP, None, importing)
    assert result.peak_power_w is None


def test_load_factor_ratio() -> None:
    # Importación: 100, 200, 300, 400 -> avg=250, peak=400 -> factor=0.625
    result = load_factor_result(START, STOP, None, _importing([100.0, 200.0, 300.0, 400.0]))
    assert result.average_import_w == 250.0
    assert result.peak_import_w == 400.0
    assert result.load_factor == 0.625


def test_load_factor_uses_real_peak_when_given() -> None:
    """F0.1: con el pico real (de la serie agregada con max) el factor de carga
    baja. Tomarlo del mismo marco promediado lo inflaba."""
    importing = _importing([100.0, 200.0, 300.0, 400.0])
    inflated = load_factor_result(START, STOP, None, importing)
    honest = load_factor_result(START, STOP, None, importing, peak_w=1000.0)
    assert honest.peak_import_w == 1000.0
    assert honest.average_import_w == 250.0
    assert honest.load_factor == 0.25
    assert inflated.load_factor == 0.625  # el mismo marco consigo mismo: pico subestimado


def test_load_factor_none_without_importing_samples() -> None:
    result = load_factor_result(START, STOP, None, _importing([-100.0, -200.0]))
    assert result.load_factor is None
    assert result.average_import_w is None


def test_base_load_percentile_of_importing_samples() -> None:
    importing = _importing([-500.0, 100.0, 200.0, 300.0, 400.0, 500.0])
    result = base_load_result(START, STOP, None, importing, percentile=0.5)
    # Mediana de [100,200,300,400,500] = 300
    assert result.base_load_w == 300.0
    assert result.percentile == 0.5


def test_base_load_none_without_importing_samples() -> None:
    result = base_load_result(START, STOP, None, _importing([-100.0]), DEFAULT_PERCENTILE)
    assert result.base_load_w is None


async def test_daily_profile_groups_by_hour() -> None:
    repo = FakeInfluxRepository()
    # Dos días, misma hora 10 -> se promedian
    repo.instant_series_points = [
        TimeSeriesPoint(time=datetime(2026, 7, 13, 10, tzinfo=UTC), value=100.0),
        TimeSeriesPoint(time=datetime(2026, 7, 14, 10, tzinfo=UTC), value=200.0),
        TimeSeriesPoint(time=datetime(2026, 7, 13, 11, tzinfo=UTC), value=50.0),
    ]
    profile = await daily_profile(repo, START, STOP, None, "UTC")
    by_hour = {p.hour: p for p in profile}
    assert by_hour[10].power_avg_w == 150.0
    assert by_hour[10].sample_count == 2
    assert by_hour[11].power_avg_w == 50.0


async def test_daily_profile_empty() -> None:
    repo = FakeInfluxRepository()
    assert await daily_profile(repo, START, STOP, None, "UTC") == []


async def test_daily_profile_groups_by_local_hour_not_utc() -> None:
    """Regresión: sin convertir a tz_name primero, `.dt.hour()` de Polars
    extrae la hora UTC. 2026-07-14T02:00:00Z es 2026-07-13 21:00 en Bogotá
    (UTC-5) — debe agruparse en la hora local 21, no en la 2."""
    repo = FakeInfluxRepository()
    repo.instant_series_points = [
        TimeSeriesPoint(time=datetime(2026, 7, 14, 2, tzinfo=UTC), value=300.0),
    ]
    profile = await daily_profile(repo, START, STOP, None, "America/Bogota")
    by_hour = {p.hour: p for p in profile}
    assert 21 in by_hour
    assert 2 not in by_hour
    assert by_hour[21].power_avg_w == 300.0


async def test_weekday_profile_averages_consumption_and_export() -> None:
    repo = FakeInfluxRepository()
    # Dos lunes distintos con consumo diferente
    repo.energy_series_points_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [
            EnergyPoint(time=datetime(2026, 7, 6, tzinfo=UTC), value=10.0),  # lunes
            EnergyPoint(time=datetime(2026, 7, 13, tzinfo=UTC), value=20.0),  # lunes
        ],
        Variable.POWER_ACTIVE_TOTAL_NEG: [
            EnergyPoint(time=datetime(2026, 7, 6, tzinfo=UTC), value=5.0),
            EnergyPoint(time=datetime(2026, 7, 13, tzinfo=UTC), value=7.0),
        ],
    }
    profile = await weekday_profile(repo, START, STOP, None, "UTC")
    monday = next(p for p in profile if p.weekday == 0)
    assert monday.weekday_name == "Lunes"
    assert monday.consumption_avg_kwh == 15.0
    assert monday.export_avg_kwh == 6.0


async def test_weekday_profile_empty() -> None:
    repo = FakeInfluxRepository()
    assert await weekday_profile(repo, START, STOP, None, "UTC") == []


async def test_weekday_profile_groups_by_local_weekday_not_utc() -> None:
    """Regresión: 2026-07-14T02:00:00Z (martes en UTC) es 2026-07-13 21:00
    en Bogotá (UTC-5) — lunes. Sin convertir a tz_name primero, este punto
    se contaría como martes, corriendo el consumo nocturno al día
    siguiente."""
    repo = FakeInfluxRepository()
    repo.energy_series_points_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [
            EnergyPoint(time=datetime(2026, 7, 14, 2, tzinfo=UTC), value=10.0),
        ],
        Variable.POWER_ACTIVE_TOTAL_NEG: [],
    }
    profile = await weekday_profile(repo, START, STOP, None, "America/Bogota")
    assert [p.weekday for p in profile] == [0]  # lunes, no martes
    assert profile[0].consumption_avg_kwh == 10.0


async def test_compare_periods_computes_deltas() -> None:
    repo = FakeInfluxRepository()
    repo.energy_total_value = 10.0
    result = await compare_periods(
        repo, START, STOP, START - timedelta(days=7), STOP - timedelta(days=7), None
    )
    assert result.period_a.consumption_kwh == 10.0
    assert result.consumption_delta_pct == 0.0  # mismo valor en ambos periodos


async def test_compare_periods_delta_none_when_baseline_zero() -> None:
    repo = FakeInfluxRepository()
    repo.energy_total_value = 0.0
    result = await compare_periods(repo, START, STOP, START, STOP, None)
    assert result.consumption_delta_pct is None
