from datetime import UTC, datetime

from app.schemas.energy import EnergySummary
from app.schemas.influx import EnergyPoint
from app.schemas.tariff import TariffConfig, TariffPeriod
from app.services.tariff.cost import compute_cost, compute_cost_from_points

JAN = TariffPeriod(month="2026-01", cu_cop_kwh=859.19, cargo_fijo_cop=9090.0)
FEB = TariffPeriod(month="2026-02", cu_cop_kwh=801.24, cargo_fijo_cop=9197.0)


def _config(*periods: TariffPeriod, excedente: float = 114.34) -> TariffConfig:
    return TariffConfig(excedente_cop_kwh=excedente, periods=list(periods))


def _summary(points: list[EnergyPoint], start: datetime, end: datetime) -> EnergySummary:
    return EnergySummary(
        period="month",
        device_id="11",
        period_start=start,
        period_end=end,
        total_kwh=round(sum(p.value for p in points), 2),
        series=points,
    )


def test_consumption_cost_uses_matching_month_rate() -> None:
    config = _config(JAN)
    consumption = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=10.0)],
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, tzinfo=UTC),
    )
    export = _summary([], consumption.period_start, consumption.period_end)

    result = compute_cost(config, "month", consumption, export, device_id="11")

    assert result.consumption_cost_cop == round(10.0 * 859.19, 2)
    assert result.months_used == ["2026-01"]
    assert result.stale_months == []


def test_export_credit_uses_excedente_rate() -> None:
    config = _config(JAN)
    consumption = _summary([], datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 31, tzinfo=UTC))
    export = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=5.0)],
        consumption.period_start,
        consumption.period_end,
    )

    result = compute_cost(config, "month", consumption, export, device_id="11")

    assert result.export_credit_cop == round(5.0 * 114.34, 2)
    assert result.net_cost_cop == round(result.cargo_fijo_cop - 5.0 * 114.34, 2)


def test_cargo_fijo_included_only_for_month_and_year() -> None:
    config = _config(JAN)
    consumption = _summary([], datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 31, tzinfo=UTC))
    export = _summary([], consumption.period_start, consumption.period_end)

    month_result = compute_cost(config, "month", consumption, export, device_id="11")
    assert month_result.cargo_fijo_included is True
    assert month_result.cargo_fijo_cop == 9090.0

    day_result = compute_cost(config, "day", consumption, export, device_id="11")
    assert day_result.cargo_fijo_included is False
    assert day_result.cargo_fijo_cop == 0.0

    week_result = compute_cost(config, "week", consumption, export, device_id="11")
    assert week_result.cargo_fijo_included is False


def test_stale_month_uses_most_recent_earlier_rate() -> None:
    """Marzo no tiene tarifa registrada: usa febrero (la más reciente
    anterior) y lo marca como stale — nunca lo oculta."""
    config = _config(JAN, FEB)
    consumption = _summary(
        [EnergyPoint(time=datetime(2026, 3, 10, tzinfo=UTC), value=20.0)],
        datetime(2026, 3, 1, tzinfo=UTC),
        datetime(2026, 3, 31, tzinfo=UTC),
    )
    export = _summary([], consumption.period_start, consumption.period_end)

    result = compute_cost(config, "month", consumption, export, device_id="11")

    assert result.consumption_cost_cop == round(20.0 * FEB.cu_cop_kwh, 2)
    assert result.months_used == ["2026-02"]
    assert "2026-03" in result.stale_months


def test_no_earlier_tariff_at_all_skips_silently_but_flags_stale() -> None:
    """Sin ninguna tarifa registrada todavía (proyecto recién instalado):
    costo 0, pero el mes queda marcado como stale para que la UI avise."""
    config = _config()  # sin periods
    consumption = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=10.0)],
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, tzinfo=UTC),
    )
    export = _summary([], consumption.period_start, consumption.period_end)

    result = compute_cost(config, "month", consumption, export, device_id="11")

    assert result.consumption_cost_cop == 0.0
    assert result.months_used == []
    assert "2026-01" in result.stale_months


def test_net_cost_can_be_negative_when_export_credit_exceeds_cost() -> None:
    config = _config(JAN)
    consumption = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=1.0)],  # barato
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, tzinfo=UTC),
    )
    export = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=1000.0)],  # mucho crédito
        consumption.period_start,
        consumption.period_end,
    )

    result = compute_cost(config, "day", consumption, export, device_id="11")

    assert result.net_cost_cop < 0


def test_series_has_one_point_per_consumption_point() -> None:
    config = _config(JAN)
    points = [
        EnergyPoint(time=datetime(2026, 1, 15, 0, tzinfo=UTC), value=1.0),
        EnergyPoint(time=datetime(2026, 1, 15, 1, tzinfo=UTC), value=2.0),
    ]
    export_points = [EnergyPoint(time=datetime(2026, 1, 15, 0, tzinfo=UTC), value=0.5)]

    result = compute_cost_from_points(
        config,
        "custom",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, tzinfo=UTC),
        "11",
        points,
        export_points,
        consumption_total=3.0,
        export_total=0.5,
        include_cargo_fijo=False,
    )

    assert len(result.series) == 2
    assert result.series[0].export_kwh == 0.5  # matched by exact timestamp
    assert result.series[1].export_kwh == 0.0  # sin exportación en ese bucket
    assert result.series[0].consumption_cost_cop == round(1.0 * 859.19, 2)


def test_export_point_without_matching_consumption_still_counts_in_total() -> None:
    """Un punto de exportación sin punto de consumo en el mismo instante no
    aparece en `series` (que itera por consumo), pero sí se contabiliza en
    el crédito total — la suma no depende del emparejamiento por tiempo."""
    config = _config(JAN)
    points = [EnergyPoint(time=datetime(2026, 1, 15, 0, tzinfo=UTC), value=1.0)]
    export_points = [
        EnergyPoint(time=datetime(2026, 1, 15, 0, tzinfo=UTC), value=0.5),
        EnergyPoint(time=datetime(2026, 1, 15, 1, tzinfo=UTC), value=0.3),
    ]

    result = compute_cost_from_points(
        config,
        "custom",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, tzinfo=UTC),
        "11",
        points,
        export_points,
        consumption_total=1.0,
        export_total=0.8,
        include_cargo_fijo=False,
    )

    assert len(result.series) == 1
    assert result.export_credit_cop == round(0.8 * 114.34, 2)


def test_cargo_fijo_touches_every_calendar_month_even_without_points() -> None:
    """El cargo fijo se debe por mes de facturación, no por presencia de
    datos: un mes sin ningún punto de consumo igual debe cobrarlo (regresión
    del bug donde `touched_months` se derivaba de `consumption_points`)."""
    config = _config(JAN)

    result = compute_cost_from_points(
        config,
        "custom",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, tzinfo=UTC),
        None,
        [],
        [],
        consumption_total=0.0,
        export_total=0.0,
        include_cargo_fijo=True,
    )

    assert result.cargo_fijo_cop == 9090.0
    assert result.months_used == ["2026-01"]


def test_cargo_fijo_spans_multiple_months_in_range() -> None:
    config = _config(JAN, FEB)

    result = compute_cost_from_points(
        config,
        "custom",
        datetime(2026, 1, 15, tzinfo=UTC),
        datetime(2026, 2, 15, tzinfo=UTC),
        None,
        [],
        [],
        consumption_total=0.0,
        export_total=0.0,
        include_cargo_fijo=True,
    )

    assert result.cargo_fijo_cop == round(9090.0 + 9197.0, 2)
    assert result.months_used == ["2026-01", "2026-02"]
