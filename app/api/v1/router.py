"""Router agregado de /api/v1. Cada dominio registra aquí su router."""

from fastapi import APIRouter

from app.api.v1.alerts import router as alerts_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.costs import router as costs_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.devices import router as devices_router
from app.api.v1.history import router as history_router
from app.api.v1.reports import router as reports_router
from app.api.v1.variables import router as variables_router
from app.schemas.common import ApiResponse

api_router = APIRouter()
api_router.include_router(dashboard_router)
api_router.include_router(history_router)
api_router.include_router(analytics_router)
api_router.include_router(reports_router)
api_router.include_router(alerts_router)
api_router.include_router(costs_router)
api_router.include_router(devices_router)
api_router.include_router(variables_router)


@api_router.get(
    "/health",
    tags=["Health"],
    summary="Estado del servicio",
    response_model=ApiResponse[dict[str, str]],
)
async def health() -> ApiResponse[dict[str, str]]:
    """Liveness check. No toca InfluxDB ni MQTT."""
    return ApiResponse(message="Service healthy", data={"status": "ok"})
