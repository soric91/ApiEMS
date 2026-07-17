"""Router agregado de /api/v1. Cada dominio registra aquí su router."""

from fastapi import APIRouter

from app.api.v1.alerts import router as alerts_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.auth import router as auth_router
from app.api.v1.consumption import router as consumption_router
from app.api.v1.costs import router as costs_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.export import router as export_router
from app.api.v1.history import router as history_router
from app.api.v1.kpis import router as kpis_router
from app.api.v1.realtime import router as realtime_router
from app.api.v1.reports import router as reports_router
from app.api.v1.tariff import router as tariff_router
from app.schemas.common import ApiResponse

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(dashboard_router)
api_router.include_router(realtime_router)
api_router.include_router(history_router)
api_router.include_router(consumption_router)
api_router.include_router(export_router)
api_router.include_router(analytics_router)
api_router.include_router(kpis_router)
api_router.include_router(reports_router)
api_router.include_router(alerts_router)
api_router.include_router(tariff_router)
api_router.include_router(costs_router)


@api_router.get(
    "/health",
    tags=["Health"],
    summary="Estado del servicio",
    response_model=ApiResponse[dict[str, str]],
)
async def health() -> ApiResponse[dict[str, str]]:
    """Liveness check. No toca InfluxDB ni MQTT."""
    return ApiResponse(message="Service healthy", data={"status": "ok"})
