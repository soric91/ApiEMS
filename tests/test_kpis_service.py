from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.models.variables import Variable
from app.schemas.influx import TimeSeriesPoint
from app.services.kpis.summary import compute_kpis
from tests.fakes import FakeInfluxRepository

START = datetime(2026, 7, 16, 0, 0, tzinfo=UTC)
STOP = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _points(values: list[float]) -> list[TimeSeriesPoint]:
    return [TimeSeriesPoint(time=START + timedelta(hours=i), value=v) for i, v in enumerate(values)]


def _settings() -> Settings:
    return Settings(_env_file=None, TIMEZONE="America/Bogota")  # pyright: ignore[reportCallIssue]


async def test_compute_kpis_combines_phases() -> None:
    repo = FakeInfluxRepository()
    repo.instant_series_by_variable = {
        Variable.POWER_ACTIVE_INST_TOTAL: _points([100.0, 200.0]),
        Variable.VOLTAGE_A: _points([120.0, 122.0]),
        Variable.VOLTAGE_B: _points([118.0, 119.0]),
        Variable.CURRENT_A: _points([1.0, 2.0]),
        Variable.CURRENT_B: _points([1.5, 2.5]),
        Variable.FACTOR_POTENCIA_TOTAL: _points([0.9, 0.95]),
    }
    repo.energy_total_value = 3.0

    result = await compute_kpis(repo, _settings(), START, STOP, None)

    assert result.power_avg_w == 150.0
    assert result.power_max_w == 200.0
    # Voltaje combina fase A y B: [120, 122, 118, 119] -> avg=119.75, min=118, max=122
    assert result.voltage_avg_v == 119.75
    assert result.voltage_min_v == 118.0
    assert result.voltage_max_v == 122.0
    # Corriente combina A y B: [1.0, 2.0, 1.5, 2.5] -> avg=1.75
    assert result.current_avg_a == 1.75
    assert result.power_factor_avg == round((0.9 + 0.95) / 2, 2)  # 0.93 (round-half-to-even)
    assert result.consumption_daily_kwh == 3.0
    assert result.export_monthly_kwh == 3.0


async def test_compute_kpis_none_when_no_samples() -> None:
    repo = FakeInfluxRepository()
    result = await compute_kpis(repo, _settings(), START, STOP, None)
    assert result.power_avg_w is None
    assert result.voltage_avg_v is None
    # La energía siempre tiene valor (el fake devuelve energy_total_value por defecto)
    assert result.consumption_daily_kwh == repo.energy_total_value
