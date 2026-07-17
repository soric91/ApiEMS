"""Provider de inyección de dependencias para la tarifa eléctrica."""

from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.schemas.tariff import TariffConfig
from app.services.tariff.store import load_tariff_config


async def get_tariff_config(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TariffConfig:
    return await load_tariff_config(settings.TARIFF_CONFIG_PATH)
