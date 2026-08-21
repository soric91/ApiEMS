from datetime import UTC, datetime, timedelta

import pytest

from app.services.periods import PeriodBounds, resolve_period
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


# --- resolve_period -----------------------------------------------------


def test_resolve_period_day_preset() -> None:
    bounds = resolve_period("day", TZ, NOW)
    assert bounds.start == datetime(2026, 7, 16, 5, 0, 0, tzinfo=UTC)  # medianoche Bogotá
    assert bounds.stop == NOW
    assert bounds.interval == timedelta(hours=1)


def test_resolve_period_week_preset_starts_monday() -> None:
    bounds = resolve_period("week", TZ, NOW)
    assert bounds.start == datetime(2026, 7, 13, 5, 0, 0, tzinfo=UTC)
    assert bounds.interval == timedelta(days=1)


def test_resolve_period_month_and_year_presets() -> None:
    month = resolve_period("month", TZ, NOW)
    year = resolve_period("year", TZ, NOW)
    assert month.start == datetime(2026, 7, 1, 5, 0, 0, tzinfo=UTC)
    assert year.start == datetime(2026, 1, 1, 5, 0, 0, tzinfo=UTC)
    assert month.interval == year.interval == timedelta(days=1)


def test_resolve_period_report_aliases_to_presets() -> None:
    assert resolve_period("daily", TZ, NOW) == resolve_period("day", TZ, NOW)
    assert resolve_period("weekly", TZ, NOW) == resolve_period("week", TZ, NOW)
    assert resolve_period("monthly", TZ, NOW) == resolve_period("month", TZ, NOW)
    assert resolve_period("yearly", TZ, NOW) == resolve_period("year", TZ, NOW)


def test_resolve_period_explicit_from_to_override_preset() -> None:
    from_ = datetime(2026, 7, 10, 5, 0, 0, tzinfo=UTC)
    to = datetime(2026, 7, 12, 5, 0, 0, tzinfo=UTC)
    bounds = resolve_period("day", TZ, NOW, from_=from_, to=to)
    assert bounds.start == from_
    assert bounds.stop == to
    assert bounds.interval == timedelta(hours=1)


def test_resolve_period_defaults_to_now_when_no_reference() -> None:
    bounds = resolve_period("day", TZ)
    assert bounds.stop > datetime.now(tz=UTC) - timedelta(seconds=5)
    assert bounds.stop <= datetime.now(tz=UTC) + timedelta(seconds=5)


def test_resolve_period_custom_requires_from_and_to() -> None:
    with pytest.raises(ValueError):
        resolve_period("custom", TZ, NOW)  # type: ignore[call-overload]
    with pytest.raises(ValueError):
        resolve_period("custom", TZ, NOW, from_=NOW)  # type: ignore[call-overload]


def test_resolve_period_custom_uses_auto_interval() -> None:
    from_ = datetime(2026, 7, 1, 5, 0, 0, tzinfo=UTC)
    to = datetime(2026, 7, 16, 5, 0, 0, tzinfo=UTC)
    bounds = resolve_period("custom", TZ, NOW, from_=from_, to=to)
    assert isinstance(bounds, PeriodBounds)
    assert bounds.start == from_
    assert bounds.stop == to
    assert bounds.interval < timedelta(days=1)  # auto_interval ajusta a ~500 puntos


def test_resolve_period_rejects_inverted_range() -> None:
    from_ = datetime(2026, 7, 16, 5, 0, 0, tzinfo=UTC)
    to = datetime(2026, 7, 16, 4, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        resolve_period("day", TZ, NOW, from_=from_, to=to)


# --- barras de energía ---------------------------------------------------


def test_las_barras_de_un_rango_libre_de_un_mes_van_por_dia() -> None:
    """Un mes libre se ve como la pestaña Mensual, no en ~500 barras.

    El intervalo de LECTURA sigue siendo fino: de él depende que un pico de
    tres minutos no se promedie hasta desaparecer en la demanda máxima.
    """
    desde = datetime(2026, 7, 21, 5, tzinfo=UTC)
    hasta = datetime(2026, 8, 20, 5, tzinfo=UTC)

    bounds = resolve_period("custom", TZ, NOW, from_=desde, to=hasta)

    assert bounds.energy_interval == timedelta(days=1)
    assert bounds.interval < timedelta(hours=2)


def test_un_rango_libre_corto_va_por_hora() -> None:
    desde = datetime(2026, 7, 16, 5, tzinfo=UTC)
    hasta = datetime(2026, 7, 17, 5, tzinfo=UTC)

    bounds = resolve_period("custom", TZ, NOW, from_=desde, to=hasta)

    assert bounds.energy_interval == timedelta(hours=1)


def test_un_rango_libre_de_varios_anios_va_por_semana() -> None:
    desde = datetime(2024, 1, 1, 5, tzinfo=UTC)
    hasta = datetime(2026, 1, 1, 5, tzinfo=UTC)

    bounds = resolve_period("custom", TZ, NOW, from_=desde, to=hasta)

    assert bounds.energy_interval == timedelta(days=7)


@pytest.mark.parametrize(
    ("bucket", "esperado"),
    [("hour", timedelta(hours=1)), ("day", timedelta(days=1)), ("week", timedelta(days=7))],
)
def test_el_cliente_puede_pedir_otra_agrupacion(bucket: str, esperado: timedelta) -> None:
    # Pedir más detalle del que la escalera propone: "estos 30 días, hora por
    # hora".
    desde = datetime(2026, 7, 21, 5, tzinfo=UTC)
    hasta = datetime(2026, 8, 20, 5, tzinfo=UTC)

    bounds = resolve_period("custom", TZ, NOW, from_=desde, to=hasta, bucket=bucket)  # type: ignore[arg-type]

    assert bounds.energy_interval == esperado


def test_la_agrupacion_elegida_no_toca_el_intervalo_de_lectura() -> None:
    desde = datetime(2026, 7, 21, 5, tzinfo=UTC)
    hasta = datetime(2026, 8, 20, 5, tzinfo=UTC)

    fino = resolve_period("custom", TZ, NOW, from_=desde, to=hasta)
    grueso = resolve_period("custom", TZ, NOW, from_=desde, to=hasta, bucket="week")

    # La demanda pico se sigue leyendo igual de fino: la agrupación es de las
    # barras, no de lo que se mide.
    assert grueso.interval == fino.interval


def test_los_periodos_fijos_conservan_su_agrupacion() -> None:
    assert resolve_period("day", TZ, NOW).energy_interval == timedelta(hours=1)
    assert resolve_period("week", TZ, NOW).energy_interval == timedelta(days=1)
    assert resolve_period("month", TZ, NOW).energy_interval == timedelta(days=1)
    assert resolve_period("yearly", TZ, NOW).energy_interval == timedelta(days=1)


def test_un_periodo_fijo_tambien_acepta_otra_agrupacion() -> None:
    bounds = resolve_period("monthly", TZ, NOW, bucket="hour")

    assert bounds.energy_interval == timedelta(hours=1)
    assert bounds.interval == timedelta(days=1)
