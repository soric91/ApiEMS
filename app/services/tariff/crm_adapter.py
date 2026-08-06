"""Adapta TariffRead (CRMBackend) a TariffConfig (ApiEMS).

Mapeo 1:1 por mes: `mes`/`valor_importado`/`valor_excedente` de CRMBackend
a `month`/`cu_cop_kwh`/`excedente_cop_kwh` de `TariffPeriod`. No conectado
todavía a `get_tariff_config()` (ver `app/services/tariff/store.py`) — falta
la autenticación de servicio de ApiEMS contra CRMBackend (Fase 5, punto 1
en prompt_arquitectura_v2.md), no un gap de modelo.
"""

from typing import Any

from app.schemas.tariff import TariffConfig, TariffPeriod

_MONTH_SLICE = slice(0, 7)  # "YYYY-MM-DD" -> "YYYY-MM"


def adapt_crm_tariffs(crm_tariffs: list[dict[str, Any]]) -> TariffConfig:
    periods = sorted(
        (
            TariffPeriod(
                month=str(item["mes"])[_MONTH_SLICE],
                cu_cop_kwh=float(item["valor_importado"]),
                excedente_cop_kwh=float(item["valor_excedente"]),
            )
            for item in crm_tariffs
        ),
        key=lambda p: p.month,
    )
    return TariffConfig(periods=periods)
