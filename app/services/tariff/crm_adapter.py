"""Adapta TariffRead (CRMBackend) a TariffConfig (ApiEMS).

GAP CONFIRMADO, no un olvido: el modelo `Tariff` de CRMBackend
(`mes`, `valor_importado`, `valor_excedente`) no tiene cargo fijo — solo
ApiEMS lo modela (`TariffPeriod.cargo_fijo_cop`). Este adaptador lo deja en
0.0 para cada periodo en vez de inventar un valor. Por eso NO está conectado
a `get_tariff_config()` todavía (ver `app/services/tariff/store.py`): usarlo
en vivo pondría el cargo fijo en 0 en cada costo mensual/anual sin que nadie
lo haya decidido explícitamente. Ver Fase 5 en prompt_arquitectura_v2.md.
"""

from typing import Any

from app.schemas.tariff import TariffConfig, TariffPeriod

_MONTH_SLICE = slice(0, 7)  # "YYYY-MM-DD" -> "YYYY-MM"


def adapt_crm_tariffs(crm_tariffs: list[dict[str, Any]]) -> TariffConfig:
    rows = sorted(
        (
            (
                str(item["mes"])[_MONTH_SLICE],
                float(item["valor_importado"]),
                float(item["valor_excedente"]),
            )
            for item in crm_tariffs
        ),
        key=lambda row: row[0],
    )
    periods = [
        TariffPeriod(month=month, cu_cop_kwh=cu_cop_kwh, cargo_fijo_cop=0.0)
        for month, cu_cop_kwh, _ in rows
    ]
    excedente_cop_kwh = rows[-1][2] if rows else 0.0
    return TariffConfig(excedente_cop_kwh=excedente_cop_kwh, periods=periods)
