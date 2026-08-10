from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.models.variables import Variable
from app.schemas.influx import EnergyPoint
from app.schemas.tariff import TariffConfig, TariffPeriod
from app.services.reports.builder import build_report
from tests.fakes import FakeInfluxRepository

_EMPTY_TARIFF = TariffConfig()


def _settings() -> Settings:
    return Settings(_env_file=None, TIMEZONE="America/Bogota")  # pyright: ignore[reportCallIssue]


async def test_build_report_fixed_periods() -> None:
    for report_type in ["daily", "weekly", "monthly", "yearly"]:
        repo = FakeInfluxRepository()
        repo.energy_total_value = 4.0
        report = await build_report(repo, _settings(), report_type, None, _EMPTY_TARIFF)  # pyright: ignore[reportArgumentType]
        assert report.report_type == report_type
        assert report.consumption_kwh == 4.0
        assert report.export_kwh == 4.0
        assert report.net_balance_kwh == 0.0
        assert report.kpis is not None
        assert report.max_demand is not None


async def test_build_report_custom_requires_bounds() -> None:
    repo = FakeInfluxRepository()
    with pytest.raises(ValueError, match="requiere from_ y to"):
        await build_report(repo, _settings(), "custom", None, _EMPTY_TARIFF)


async def test_build_report_custom_uses_given_range() -> None:
    repo = FakeInfluxRepository()
    repo.energy_total_value = 1.0
    start = datetime(2026, 1, 1, tzinfo=UTC)
    stop = datetime(2026, 1, 31, tzinfo=UTC)
    report = await build_report(repo, _settings(), "custom", None, _EMPTY_TARIFF, start, stop)
    assert report.period_start == start
    assert report.period_end == stop


async def test_build_report_net_balance() -> None:
    repo = FakeInfluxRepository()
    repo.energy_total_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: 10.0,
        Variable.POWER_ACTIVE_TOTAL_NEG: 3.0,
    }
    report = await build_report(repo, _settings(), "daily", None, _EMPTY_TARIFF)
    assert report.consumption_kwh == 10.0
    assert report.export_kwh == 3.0
    assert report.net_balance_kwh == 7.0


async def test_build_report_costs_without_tariff_is_zero_and_stale() -> None:
    repo = FakeInfluxRepository()
    repo.energy_total_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: 10.0,
        Variable.POWER_ACTIVE_TOTAL_NEG: 0.0,
    }
    report = await build_report(repo, _settings(), "monthly", None, _EMPTY_TARIFF)
    assert report.costs.consumption_cost_cop == 0.0
    assert len(report.costs.stale_months) >= 1


async def test_build_report_costs_uses_configured_tariff() -> None:
    config = TariffConfig(
        periods=[TariffPeriod(month="2026-01", cu_cop_kwh=859.19, excedente_cop_kwh=114.34)],
    )

    repo = FakeInfluxRepository()
    repo.energy_series_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [
            EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=10.0)
        ],
        Variable.POWER_ACTIVE_TOTAL_NEG: [],
    }
    repo.energy_total_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: 10.0,
        Variable.POWER_ACTIVE_TOTAL_NEG: 0.0,
    }
    start = datetime(2026, 1, 1, tzinfo=UTC)
    stop = datetime(2026, 1, 31, tzinfo=UTC)

    report = await build_report(repo, _settings(), "custom", None, config, start, stop)

    assert report.costs.period == "custom"
    assert report.costs.consumption_cost_cop == round(10.0 * 859.19, 2)
    assert report.costs.series[0].consumption_kwh == 10.0
