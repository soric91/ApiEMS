"""Adapta TariffRead (CRMBackend) a TariffConfig (ApiEMS).

Mapeo 1:1 por mes: `mes`/`valor_importado`/`valor_excedente` de CRMBackend
a `month`/`cu_cop_kwh`/`excedente_cop_kwh` de `TariffPeriod`. Conectado vía
`RemoteTariffStore` (`app/services/tariff/store.py`), que usa esto para
traducir la respuesta de `CrmClient.get_tariffs()`.
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
