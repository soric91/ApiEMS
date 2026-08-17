"""Proyecciones: en qué termina el mes si el consumo sigue como viene."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentFleet
from app.dependencies.influx import get_influx_repository
from app.dependencies.tariff import get_tariff_config
from app.repositories.scoped import ScopedInfluxRepository
from app.schemas.common import ApiResponse
from app.schemas.forecast import BillForecast, PowerForecast
from app.schemas.tariff import TariffConfig
from app.services.forecast.bill import bill_forecast
from app.services.forecast.power import (
    DEFAULT_HORIZON_HOURS,
    MAX_HORIZON_HOURS,
    power_forecast,
)

router = APIRouter(prefix="/forecast", tags=["Forecast"])

RepoDep = Annotated[ScopedInfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
TariffDep = Annotated[TariffConfig, Depends(get_tariff_config)]


@router.get(
    "/bill",
    summary="Proyección de la factura del mes en curso",
    response_model=ApiResponse[BillForecast],
)
async def forecast_bill(
    repo: RepoDep,
    settings: SettingsDep,
    tariff: TariffDep,
    fleet: CurrentFleet,
    device_id: str | None = None,
) -> ApiResponse[BillForecast]:
    """Cuánto va del mes y en cuánto termina al ritmo actual.

    Proyecta con la media exponencial de los últimos 28 días separando tipos de
    día (un lunes no se parece a un domingo), y la banda p10-p90 sale de los
    días buenos y malos que ya ocurrieron. Con menos de 14 días completos de
    historial devuelve `method: "insufficient_history"` y no proyecta nada.

    El costo lo calcula el mismo motor de tarifa que /costs y /reports, así que
    los dos tramos del excedente se reparten igual que en la factura real.
    """
    return ApiResponse(data=await bill_forecast(repo, settings, tariff, device_id))


@router.get(
    "/power",
    summary="Consumo esperado por hora en las próximas horas",
    response_model=ApiResponse[PowerForecast],
)
async def forecast_power(
    repo: RepoDep,
    settings: SettingsDep,
    fleet: CurrentFleet,
    horizon_hours: Annotated[int, Query(gt=0, le=MAX_HORIZON_HOURS)] = DEFAULT_HORIZON_HOURS,
    device_id: str | None = None,
) -> ApiResponse[PowerForecast]:
    """Cuánta energía se espera importar en cada una de las próximas horas.

    Cada hora se predice con la media exponencial de esa misma hora en los días
    del mismo tipo, y la banda p10-p90 sale de la dispersión real de esos días.
    Es el "seasonal naive" con memoria: en el modelo entrenado de energyML el
    retardo de 24 h pesa 0.72 de la importancia total, así que esto es casi
    todo lo que ese modelo sabe.

    La respuesta trae su propio `backtest`: el error del método sobre días que
    NO entraron en el cálculo, junto al del ingenuo "lo mismo que hace 24 h".
    Con ese número a la vista se puede cambiar de método —o traer el modelo
    entrenado— y demostrar que mejora, en vez de suponerlo.
    """
    return ApiResponse(
        data=await power_forecast(repo, settings, device_id, horizon_hours)
    )
