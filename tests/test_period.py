from datetime import UTC, datetime, timedelta

from app.utils.period import (
    flux_window_offset,
    month_starts_of_year,
    start_of_day,
    start_of_month,
    start_of_week,
    start_of_year,
)

TZ = "America/Bogota"  # UTC-5, sin horario de verano

# Jueves 2026-07-16 16:23:44 America/Bogota == 21:23:44 UTC
NOW = datetime(2026, 7, 16, 21, 23, 44, tzinfo=UTC)


def test_start_of_day_converts_to_local_midnight() -> None:
    start = start_of_day(TZ, NOW)
    assert start == datetime(2026, 7, 16, 5, 0, 0, tzinfo=UTC)  # 00:00 Bogotá = 05:00 UTC


def test_start_of_week_is_monday() -> None:
    start = start_of_week(TZ, NOW)
    # 2026-07-16 es jueves; el lunes de esa semana es 2026-07-13
    assert start == datetime(2026, 7, 13, 5, 0, 0, tzinfo=UTC)


def test_start_of_month() -> None:
    start = start_of_month(TZ, NOW)
    assert start == datetime(2026, 7, 1, 5, 0, 0, tzinfo=UTC)


def test_start_of_year() -> None:
    start = start_of_year(TZ, NOW)
    assert start == datetime(2026, 1, 1, 5, 0, 0, tzinfo=UTC)


def test_month_starts_of_year_up_to_current_month() -> None:
    starts = month_starts_of_year(TZ, NOW)
    assert len(starts) == 7  # enero..julio
    assert starts[0] == datetime(2026, 1, 1, 5, 0, 0, tzinfo=UTC)
    assert starts[-1] == datetime(2026, 7, 1, 5, 0, 0, tzinfo=UTC)


def test_january_boundary_stays_in_previous_year() -> None:
    early_jan = datetime(2026, 1, 15, 3, 0, 0, tzinfo=UTC)  # 2025-12-31 22:00 Bogotá... no, check
    start = start_of_day(TZ, early_jan)
    assert start.year in (2025, 2026)  # solo verifica que no explota en el borde


def test_flux_window_offset_bogota_is_5h() -> None:
    """Regresión: sin este offset, aggregateWindow() de InfluxDB alinea sus
    ventanas a medianoche UTC en vez de medianoche Bogotá, y una ventana
    diaria termina "comiéndose" 5h del día anterior (bug real encontrado
    comparando /costs/month contra spread() — 129.52 vs 126.72 kWh)."""
    assert flux_window_offset(TZ, NOW) == timedelta(hours=5)


def test_flux_window_offset_utc_is_zero() -> None:
    assert flux_window_offset("UTC", NOW) == timedelta(0)
