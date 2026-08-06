from app.services.tariff.crm_adapter import adapt_crm_tariffs


def test_adapt_crm_tariffs_maps_fields_and_sorts_by_month() -> None:
    crm_tariffs = [
        {"mes": "2026-07-01", "valor_importado": "950.5", "valor_excedente": "120.0"},
        {"mes": "2026-06-01", "valor_importado": "902.28", "valor_excedente": "114.34"},
    ]

    config = adapt_crm_tariffs(crm_tariffs)

    assert [p.month for p in config.periods] == ["2026-06", "2026-07"]
    assert config.periods[0].cu_cop_kwh == 902.28
    assert config.periods[1].cu_cop_kwh == 950.5
    # excedente_cop_kwh es por mes, igual que cu_cop_kwh — sin cargo fijo,
    # ese concepto no existe en este mercado.
    assert config.periods[0].excedente_cop_kwh == 114.34
    assert config.periods[1].excedente_cop_kwh == 120.0


def test_adapt_crm_tariffs_empty_list() -> None:
    config = adapt_crm_tariffs([])
    assert config.periods == []
