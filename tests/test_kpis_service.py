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


class RecordingRepo(FakeInfluxRepository):
    """Igual que el fake base pero además graba los rangos de energy_total."""

    def __init__(self) -> None:
        super().__init__()
        self.energy_ranges: list[tuple[datetime, datetime]] = []

    async def energy_total(
        self,
        counter: Variable,
        start: datetime,
        stop: datetime,
        device_id: str | None = None,
        devices: object | None = None,
    ) -> float:
        self.energy_ranges.append((start, stop))
        return await super().energy_total(counter, start, stop, device_id, devices=devices)


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


async def test_compute_kpis_consumption_stays_within_requested_range() -> None:
    """F2.3: en un rango pasado, los consumption_* se calculan dentro del
    rango pedido, no contra la fecha real de 'hoy'."""
    repo = RecordingRepo()
    start = datetime(2026, 6, 16, 0, 0, tzinfo=UTC)
    stop = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)

    result = await compute_kpis(repo, _settings(), start, stop, None)

    # Las 5 consultas de energía ocurren dentro del rango pedido: ningún
    # tramo toca 'hoy' (2026-07) ni se sale de [start, stop].
    assert repo.energy_ranges
    for range_start, range_stop in repo.energy_ranges:
        assert range_start >= start
        assert range_stop <= stop

    # Dos tramos distintos, ambos dentro del rango:
    #  - día (y export diaria) = medianoche local del día de `stop` (05:00 UTC en Bogotá);
    #  - semana/mes (y export mensual) = recortados al inicio del rango
    #    (jueves a mitad de mes → el tramo solo cubre lo pedido).
    assert set(repo.energy_ranges) == {
        (datetime(2026, 6, 16, 5, 0, tzinfo=UTC), stop),
        (start, stop),
    }

    # Los valores de consumo/export salen del fake (iguales al default).
    assert result.consumption_daily_kwh == repo.energy_total_value
    assert result.consumption_weekly_kwh == repo.energy_total_value
    assert result.consumption_monthly_kwh == repo.energy_total_value
