"""Persistencia de la tarifa en un archivo JSON editable (no `.env`).

Se lee/escribe con `asyncio.to_thread` para no bloquear el loop — el
archivo es pequeño (unos pocos KB), pero mantiene el estilo async del resto
del proyecto.
"""

import asyncio
from pathlib import Path

from app.schemas.tariff import TariffConfig

_EMPTY = TariffConfig(excedente_cop_kwh=0.0)


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
