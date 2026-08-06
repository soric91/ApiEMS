"""Persistencia de la tarifa en un archivo JSON editable (no `.env`).

Se lee/escribe con `asyncio.to_thread` para no bloquear el loop — el
archivo es pequeño (unos pocos KB), pero mantiene el estilo async del resto
del proyecto.

Fase 5 (prompt_arquitectura_v2.md) pedía reemplazar este archivo por
CRMBackend (`GET /api/v1/tariffs`). El cliente y el adaptador ya existen
(`app/services/crm/client.py`, `app/services/tariff/crm_adapter.py`) y ya
NO hay gap de modelo entre los dos lados — `Tariff` de CRMBackend
(`mes`, `valor_importado`, `valor_excedente`) mapea 1:1 con `TariffPeriod`
(`month`, `cu_cop_kwh`, `excedente_cop_kwh`) desde que se sacó el cargo fijo
de este proyecto. `load_tariff_config` sigue leyendo el JSON local de
todos modos: falta resolver la autenticación de servicio de ApiEMS contra
CRMBackend (Fase 5, punto 1) antes de conmutar la fuente en vivo.
"""

import asyncio
from pathlib import Path

from app.schemas.tariff import TariffConfig

_EMPTY = TariffConfig()


def _read_if_exists(path: str) -> str | None:
    file = Path(path)
    return file.read_text() if file.exists() else None


async def load_tariff_config(path: str) -> TariffConfig:
    raw = await asyncio.to_thread(_read_if_exists, path)
    if raw is None:
        return _EMPTY
    return TariffConfig.model_validate_json(raw)


async def save_tariff_config(path: str, config: TariffConfig) -> None:
    file = Path(path)
    await asyncio.to_thread(file.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(file.write_text, config.model_dump_json(indent=2) + "\n")
