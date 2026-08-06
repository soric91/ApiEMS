"""Configuración de tarifa eléctrica — editable sin redeploy.

El usuario consulta la tarifa vigente en la web de su comercializador cada
mes y la actualiza acá; se persiste en un archivo (ver TARIFF_CONFIG_PATH),
no en `.env`.

**Desacoplado de los costos reales desde la Fase 5**: `/costs`, `/reports` y
`/analytics/summary` ya no leen este archivo — leen CRMBackend en vivo
(`app/dependencies/tariff.py`, `RemoteTariffStore`). La credencial de
servicio de ApiEMS es de solo lectura, así que este endpoint no puede
escribir en CRMBackend — editar acá solo cambia este archivo local, que hoy
no lo lee nadie más que este mismo endpoint. Pendiente decidir si se
deprecia o pasa a ser un proxy hacia CRMBackend.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentUser
from app.schemas.common import ApiResponse
from app.schemas.tariff import TariffConfig
from app.services.tariff.store import load_tariff_config, save_tariff_config

router = APIRouter(prefix="/tariff", tags=["Tariff"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("", summary="Tarifa configurada", response_model=ApiResponse[TariffConfig])
async def get_tariff(settings: SettingsDep, _user: CurrentUser) -> ApiResponse[TariffConfig]:
    """Historial de tarifas por mes + crédito de excedentes vigente."""
    config = await load_tariff_config(settings.TARIFF_CONFIG_PATH)
    return ApiResponse(data=config)


@router.put("", summary="Actualizar tarifa", response_model=ApiResponse[TariffConfig])
async def update_tariff(
    body: TariffConfig, settings: SettingsDep, _user: CurrentUser
) -> ApiResponse[TariffConfig]:
    """Reemplaza la configuración completa (historial de meses + excedente).
    Para agregar un mes nuevo, enviar el `GET` actual con el mes añadido a
    `periods` — no hay merge parcial en el servidor.
    """
    await save_tariff_config(settings.TARIFF_CONFIG_PATH, body)
    return ApiResponse(message="Tarifa actualizada", data=body)
