from datetime import UTC, datetime

from app.core.config import Settings
from app.models.variables import Variable
from app.schemas.influx import TimeSeriesPoint
from app.schemas.tariff import TariffConfig, TariffPeriod
from app.services.analytics.summary import analytics_summary
from tests.fakes import FakeInfluxRepository

START = datetime(2026, 6, 1, tzinfo=UTC)
STOP = datetime(2026, 7, 1, tzinfo=UTC)

_EMPTY_TARIFF = TariffConfig()


def _settings() -> Settings:
    return Settings(_env_file=None, TIMEZONE="America/Bogota")  # pyright: ignore[reportCallIssue]


def _hourly_points() -> list[TimeSeriesPoint]:
    return [
        TimeSeriesPoint(time=datetime(2026, 6, 15, 10, tzinfo=UTC), value=800.0),
        TimeSeriesPoint(time=datetime(2026, 6, 15, 13, tzinfo=UTC), value=-600.0),
        TimeSeriesPoint(time=datetime(2026, 6, 15, 20, tzinfo=UTC), value=200.0),
    ]


async def test_analytics_summary_peak_hours() -> None:
    """Puntos en hora UTC 10/13/20 -> Bogotá (UTC-5) 5/8/15. Confirma que el
    perfil horario agrupa por hora LOCAL, no UTC."""
    repo = FakeInfluxRepository()
    repo.instant_series_points = _hourly_points()
    report = await analytics_summary(repo, _settings(), START, STOP, None, _EMPTY_TARIFF)
    assert report.peak_consumption_hour == 5
    assert report.peak_export_hour == 8


async def test_analytics_summary_no_export_hours_peak_export_none() -> None:
    repo = FakeInfluxRepository()
    repo.instant_series_points = [
        TimeSeriesPoint(time=datetime(2026, 6, 15, 10, tzinfo=UTC), value=800.0),
    ]
    report = await analytics_summary(repo, _settings(), START, STOP, None, _EMPTY_TARIFF)
    assert report.peak_consumption_hour == 5
    assert report.peak_export_hour is None


async def test_analytics_summary_empty_profile_both_none() -> None:
    repo = FakeInfluxRepository()
    report = await analytics_summary(repo, _settings(), START, STOP, None, _EMPTY_TARIFF)
    assert report.peak_consumption_hour is None
    assert report.peak_export_hour is None
    assert report.hourly_profile == []


async def test_analytics_summary_efficiency_without_tariff_is_none() -> None:
    repo = FakeInfluxRepository()
    report = await analytics_summary(repo, _settings(), START, STOP, None, _EMPTY_TARIFF)
    assert report.efficiency is None


async def test_analytics_summary_efficiency_uses_current_month_tariff() -> None:
    now = datetime.now(tz=UTC)
    month = f"{now.year:04d}-{now.month:02d}"
    config = TariffConfig(
        periods=[TariffPeriod(month=month, cu_cop_kwh=902.28, excedente_cop_kwh=114.34)],
    )

    repo = FakeInfluxRepository()
    # consumption_monthly_kwh usa el default del fake (energy_total_value=5.5);
    # export_monthly_kwh=10.0 -> tramo 2 = 10.0 - 5.5 = 4.5 (lo que de verdad
    # se habría ahorrado, no el excedente total).
    repo.energy_total_by_counter = {Variable.POWER_ACTIVE_TOTAL_NEG: 10.0}

    report = await analytics_summary(repo, _settings(), START, STOP, None, config)

    assert report.efficiency is not None
    assert report.efficiency.stale is False
    assert report.efficiency.export_kwh == 10.0
    assert report.efficiency.potential_savings_cop == round(4.5 * (902.28 - 114.34), 2)


async def test_analytics_summary_efficiency_flags_stale_month() -> None:
    config = TariffConfig(
        periods=[TariffPeriod(month="2020-01", cu_cop_kwh=500.0, excedente_cop_kwh=100.0)],
    )

    repo = FakeInfluxRepository()
    report = await analytics_summary(repo, _settings(), START, STOP, None, config)

    assert report.efficiency is not None
    assert report.efficiency.stale is True
