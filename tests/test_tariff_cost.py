from datetime import UTC, datetime

from app.schemas.energy import EnergySummary
from app.schemas.influx import EnergyPoint
from app.schemas.tariff import TariffConfig, TariffPeriod
from app.services.tariff.cost import compute_cost, compute_cost_from_points

JAN = TariffPeriod(month="2026-01", cu_cop_kwh=859.19, excedente_cop_kwh=114.34)
FEB = TariffPeriod(month="2026-02", cu_cop_kwh=801.24, excedente_cop_kwh=100.0)


def _config(*periods: TariffPeriod) -> TariffConfig:
    return TariffConfig(periods=list(periods))


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


def test_export_credit_uses_excedente_rate_when_no_import() -> None:
    """Sin nada importado ese mes, TODO el excedente cae en tramo 2 (no hay
    tramo 1 posible: min(0, exportado) == 0)."""
    config = _config(JAN)
    consumption = _summary([], datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 31, tzinfo=UTC))
    export = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=5.0)],
        consumption.period_start,
        consumption.period_end,
    )

    result = compute_cost(config, "month", consumption, export, device_id="11")

    assert result.export_credit_cop == round(5.0 * 114.34, 2)
    assert result.net_cost_cop == round(-5.0 * 114.34, 2)


def test_export_within_import_pays_import_rate_tier1_only() -> None:
    """Ejemplo real: 100 kWh importados, 12 kWh exportados en el mismo mes —
    los 12 caen enteros en tramo 1 (12 < 100), se pagan al precio de
    importación, no al de excedente."""
    config = _config(JAN)
    consumption = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=100.0)],
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, tzinfo=UTC),
    )
    export = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=12.0)],
        consumption.period_start,
        consumption.period_end,
    )

    result = compute_cost(config, "month", consumption, export, device_id="11")

    assert result.export_credit_cop == round(12.0 * JAN.cu_cop_kwh, 2)


def test_export_beyond_import_splits_tier1_and_tier2() -> None:
    """120 kWh importados, 150 kWh exportados en el mismo mes: 120 al precio
    de importación (tramo 1), los 30 restantes al precio de excedente
    (tramo 2)."""
    config = _config(JAN)
    consumption = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=120.0)],
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, tzinfo=UTC),
    )
    export = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=150.0)],
        consumption.period_start,
        consumption.period_end,
    )

    result = compute_cost(config, "month", consumption, export, device_id="11")

    expected = round(120.0 * JAN.cu_cop_kwh + 30.0 * JAN.excedente_cop_kwh, 2)
    assert result.export_credit_cop == expected


def test_tier_split_is_published_not_just_used() -> None:
    """F3.4: el reparto en tramos ya se calculaba para el crédito; ahora sale
    en la respuesta. Es la parte que más confunde de la factura — sin verla
    separada, "exporté 150 kWh y me acreditaron poco" no tiene explicación."""
    config = _config(JAN)
    consumption = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=120.0)],
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, tzinfo=UTC),
    )
    export = _summary(
        [EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=150.0)],
        consumption.period_start,
        consumption.period_end,
    )

    result = compute_cost(config, "month", consumption, export, device_id="11")

    assert result.export_tier1_kwh == 120.0
    assert result.export_tier2_kwh == 30.0
    assert result.export_tier1_credit_cop == round(120.0 * JAN.cu_cop_kwh, 2)
    assert result.export_tier2_credit_cop == round(30.0 * JAN.excedente_cop_kwh, 2)
    # Los dos tramos suman exactamente el crédito publicado: si divergieran,
    # la cascada de la factura no cerraría contra su propio total.
    assert (
        round(result.export_tier1_credit_cop + result.export_tier2_credit_cop, 2)
        == result.export_credit_cop
    )


def test_tier_split_sums_the_credit_across_months() -> None:
    """Los tramos se resuelven por mes calendario: un rango de dos meses tiene
    dos repartos distintos que igual tienen que sumar el crédito total."""
    config = _config(JAN, FEB)
    consumption = _summary(
        [
            EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=100.0),
            EnergyPoint(time=datetime(2026, 2, 15, tzinfo=UTC), value=50.0),
        ],
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 2, 28, tzinfo=UTC),
    )
    export = _summary(
        [
            EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=180.0),
            EnergyPoint(time=datetime(2026, 2, 15, tzinfo=UTC), value=20.0),
        ],
        consumption.period_start,
        consumption.period_end,
    )

    result = compute_cost(config, "custom", consumption, export, device_id="11")

    # Enero: 100 en tramo 1, 80 en tramo 2. Febrero: los 20 caben en tramo 1.
    assert result.export_tier1_kwh == 120.0
    assert result.export_tier2_kwh == 80.0
    assert (
        round(result.export_tier1_credit_cop + result.export_tier2_credit_cop, 2)
        == result.export_credit_cop
    )


def test_tier1_never_exceeds_the_reported_import() -> None:
    """El caso real que apareció en un informe: 139.93 kWh en el tramo 1 con
    139.61 kWh importados.

    Los puntos de la serie salen de un `difference()` por ventana y el total de
    uno sobre el rango entero; no coinciden al decimal. Si los tramos se
    calculan con una fuente y el total se muestra con la otra, el informe deja
    de cuadrar consigo mismo."""
    config = _config(JAN)
    # La serie suma 139.93 pero el total real del rango es 139.61.
    consumption = EnergySummary(
        period="month",
        device_id="11",
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 1, 31, tzinfo=UTC),
        total_kwh=139.61,
        series=[
            EnergyPoint(time=datetime(2026, 1, 10, tzinfo=UTC), value=70.0),
            EnergyPoint(time=datetime(2026, 1, 20, tzinfo=UTC), value=69.93),
        ],
    )
    export = EnergySummary(
        period="month",
        device_id="11",
        period_start=consumption.period_start,
        period_end=consumption.period_end,
        total_kwh=144.13,
        series=[EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=144.4)],
    )

    result = compute_cost(config, "month", consumption, export, device_id="11")

    assert result.export_tier1_kwh <= result.consumption_kwh
    assert result.export_tier1_kwh == 139.61
    # Los dos tramos siguen sumando exactamente lo exportado que se publica.
    assert round(result.export_tier1_kwh + result.export_tier2_kwh, 2) == result.export_kwh


def test_tier_split_keeps_month_proportions_across_months() -> None:
    """Con varios meses, el ajuste conserva el reparto entre ellos: es la única
    fuente que sabe qué mes consumió qué, y cada mes tiene su propia tarifa."""
    config = _config(JAN, FEB)
    consumption = EnergySummary(
        period="month",
        device_id="11",
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 2, 28, tzinfo=UTC),
        total_kwh=150.0,  # la serie suma 152, el total manda
        series=[
            EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=101.0),
            EnergyPoint(time=datetime(2026, 2, 15, tzinfo=UTC), value=51.0),
        ],
    )
    export = EnergySummary(
        period="month",
        device_id="11",
        period_start=consumption.period_start,
        period_end=consumption.period_end,
        total_kwh=40.0,
        series=[
            EnergyPoint(time=datetime(2026, 1, 15, tzinfo=UTC), value=30.0),
            EnergyPoint(time=datetime(2026, 2, 15, tzinfo=UTC), value=10.0),
        ],
    )

    result = compute_cost(config, "custom", consumption, export, device_id="11")

    # Todo lo exportado cabe en el tramo 1 de su mes (cada mes importó más de
    # lo que exportó), así que el crédito usa el precio de compra de cada mes.
    assert result.export_tier2_kwh == 0.0
    assert round(result.export_tier1_kwh, 2) == 40.0
    esperado = round(30.0 * JAN.cu_cop_kwh + 10.0 * FEB.cu_cop_kwh, 2)
    assert result.export_tier1_credit_cop == esperado


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
    )

    assert len(result.series) == 1
    # Importado del mes (1.0) > exportado del mes (0.8): todo tramo 1, al
    # precio de importación — no al de excedente.
    assert result.export_credit_cop == round(0.8 * JAN.cu_cop_kwh, 2)


def test_cargo_fijo_no_existe() -> None:
    """El mercado no cobra cargo fijo — CostBreakdown no tiene ese campo."""
    config = _config(JAN)
    consumption = _summary([], datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 31, tzinfo=UTC))
    export = _summary([], consumption.period_start, consumption.period_end)

    result = compute_cost(config, "month", consumption, export, device_id="11")

    assert not hasattr(result, "cargo_fijo_cop")
    assert not hasattr(result, "cargo_fijo_included")
