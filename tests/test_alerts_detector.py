from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.models.variables import Variable
from app.schemas.influx import EnergyPoint, TimeSeriesPoint
from app.schemas.mqtt import DeviceReading
from app.services.alerts.detector import check_daily_total, check_hourly
from tests.fakes import FakeInfluxRepository

TZ = "UTC"


def _settings() -> Settings:
    return Settings(_env_file=None, TIMEZONE=TZ)  # pyright: ignore[reportCallIssue]


def _reading(hour: int, value: float, day: int = 20) -> DeviceReading:
    return DeviceReading(
        device_name="Modbus_DTSU666_11",
        device_id=11,
        identify_device="bf6a469f-4c2a-4402-9438-49a491ad2238",
        device_type="CT_Meter",
        timestamp=datetime(2026, 4, day, hour, tzinfo=UTC),
        success=True,
        error=None,
        data={"TotW": value},
    )


def _baseline_points(hour: int, values: list[float]) -> list[TimeSeriesPoint]:
    return [
        TimeSeriesPoint(time=datetime(2026, 3, 1 + i, hour, tzinfo=UTC), value=v)
        for i, v in enumerate(values)
    ]


async def test_check_hourly_detects_anomaly() -> None:
    repo = FakeInfluxRepository()
    repo.instant_series_by_variable = {
        Variable.POWER_ACTIVE_INST_TOTAL: _baseline_points(10, [float(v) for v in range(90, 115)])
    }
    reading = _reading(hour=10, value=5000.0)  # muy fuera de [~90,~114]
    alert = await check_hourly(repo, reading, _settings())
    assert alert is not None
    assert alert.kind == "hourly_power"
    assert alert.severity == "high"
    assert alert.bucket == 10
    assert alert.device_id == "bf6a469f-4c2a-4402-9438-49a491ad2238"


async def test_check_hourly_normal_value_no_alert() -> None:
    repo = FakeInfluxRepository()
    repo.instant_series_by_variable = {
        Variable.POWER_ACTIVE_INST_TOTAL: _baseline_points(10, [float(v) for v in range(90, 115)])
    }
    reading = _reading(hour=10, value=100.0)  # dentro de la banda
    assert await check_hourly(repo, reading, _settings()) is None


async def test_check_hourly_missing_variable_no_alert() -> None:
    repo = FakeInfluxRepository()
    reading = DeviceReading(
        device_name="d",
        device_id=11,
        identify_device="x",
        device_type="CT_Meter",
        timestamp=datetime(2026, 4, 20, 10, tzinfo=UTC),
        success=True,
        error=None,
        data={"PhV_phsA": 120.0},  # sin POWER_ACTIVE_INST_TOTAL
    )
    assert await check_hourly(repo, reading, _settings()) is None


async def test_check_hourly_no_baseline_no_alert() -> None:
    repo = FakeInfluxRepository()  # sin datos históricos configurados
    reading = _reading(hour=10, value=99999.0)
    assert await check_hourly(repo, reading, _settings()) is None


async def test_check_hourly_exporting_never_alerts() -> None:
    """Exportar excedente (valor <= 0) es siempre favorable, nunca alerta —
    sin importar cuánto se aleje de la banda histórica."""
    repo = FakeInfluxRepository()
    repo.instant_series_by_variable = {
        Variable.POWER_ACTIVE_INST_TOTAL: _baseline_points(10, [float(v) for v in range(90, 115)])
    }
    reading = _reading(hour=10, value=-9999.0)  # exportando muchísimo
    assert await check_hourly(repo, reading, _settings()) is None

    zero_reading = _reading(hour=10, value=0.0)
    assert await check_hourly(repo, zero_reading, _settings()) is None


async def test_check_hourly_sign_flip_is_always_high_severity() -> None:
    """Hora que históricamente exporta (banda toda negativa) pero ahora
    importa: alerta 'high' directa, sin importar la magnitud."""
    repo = FakeInfluxRepository()
    # Banda con p90 < 0: a esta hora casi siempre se exporta (mediodía solar)
    repo.instant_series_by_variable = {
        Variable.POWER_ACTIVE_INST_TOTAL: _baseline_points(
            12, [-v for v in range(400, 425)]
        )  # todos negativos: p90 también negativo
    }
    barely_importing = _reading(hour=12, value=5.0)  # importando poquísimo
    alert = await check_hourly(repo, barely_importing, _settings())
    assert alert is not None
    assert alert.severity == "high"
    assert "exportando" in alert.message
    assert "solar" in alert.message


async def test_check_hourly_mixed_hour_positive_within_band_no_alert() -> None:
    """Hora de transición (a veces exporta, a veces importa un poco): un
    valor positivo dentro de la banda histórica (que incluye el 0) sigue
    siendo normal."""
    repo = FakeInfluxRepository()
    mixed_values = [-50.0, -30.0, -10.0, 10.0, 30.0, 50.0] * 5  # p10<0<p90, 30 muestras
    repo.instant_series_by_variable = {
        Variable.POWER_ACTIVE_INST_TOTAL: _baseline_points(7, mixed_values)
    }
    reading = _reading(hour=7, value=20.0)  # positivo pero dentro del rango típico
    assert await check_hourly(repo, reading, _settings()) is None


async def test_check_daily_total_detects_anomaly() -> None:
    repo = FakeInfluxRepository()
    mondays = [datetime(2026, 3, 2, tzinfo=UTC) + timedelta(weeks=i) for i in range(4)]
    repo.energy_series_points_by_counter = {
        Variable.POWER_ACTIVE_TOTAL_POS: [
            EnergyPoint(time=t, value=10.0 + i) for i, t in enumerate(mondays)
        ]
    }
    repo.energy_total_by_counter = {Variable.POWER_ACTIVE_TOTAL_POS: 500.0}
    settings = _settings()
    now = datetime(2026, 3, 3, tzinfo=UTC)  # martes -> "ayer" = lunes 2 de marzo
    alert = await check_daily_total(repo, settings, device_id="11", now=now)
    assert alert is not None
    assert alert.kind == "daily_total"
    assert alert.severity == "high"
    assert alert.bucket == 0  # lunes


async def test_check_daily_total_no_baseline_no_alert() -> None:
    repo = FakeInfluxRepository()
    now = datetime(2026, 3, 3, tzinfo=UTC)
    alert = await check_daily_total(repo, _settings(), device_id="11", now=now)
    assert alert is None
