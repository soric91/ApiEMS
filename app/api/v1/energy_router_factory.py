"""Factory de router para /consumption y /export.

Mismos 4 endpoints (day/week/month/year); solo cambia qué contador
consultan (importación vs exportación). Evita duplicar la definición de
rutas entre los dos routers.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentUser
from app.dependencies.influx import get_influx_repository
from app.models.variables import Variable
from app.repositories.influx import InfluxRepository
from app.schemas.common import ApiResponse
from app.schemas.energy import EnergySummary, Period
from app.services.energy.summary import period_summary

RepoDep = Annotated[InfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def build_energy_router(*, prefix: str, tag: str, counter: Variable, noun: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    async def _summary(
        period: Period, repo: InfluxRepository, settings: Settings, device_id: str | None
    ) -> ApiResponse[EnergySummary]:
        summary = await period_summary(repo, counter, period, settings.TIMEZONE, device_id)
        return ApiResponse(data=summary)

    @router.get(
        "/day",
        summary=f"{noun} de hoy",
        description=(
            f"{noun} desde el inicio del día calendario (zona horaria configurada) "
            "hasta ahora. Desglose por hora."
        ),
        response_model=ApiResponse[EnergySummary],
    )
    async def day(  # pyright: ignore[reportUnusedFunction]
        repo: RepoDep, settings: SettingsDep, _user: CurrentUser, device_id: str | None = None
    ) -> ApiResponse[EnergySummary]:
        return await _summary("day", repo, settings, device_id)

    @router.get(
        "/week",
        summary=f"{noun} de la semana",
        description=f"{noun} desde el lunes de esta semana hasta ahora. Desglose diario.",
        response_model=ApiResponse[EnergySummary],
    )
    async def week(  # pyright: ignore[reportUnusedFunction]
        repo: RepoDep, settings: SettingsDep, _user: CurrentUser, device_id: str | None = None
    ) -> ApiResponse[EnergySummary]:
        return await _summary("week", repo, settings, device_id)

    @router.get(
        "/month",
        summary=f"{noun} del mes",
        description=f"{noun} desde el día 1 del mes hasta ahora. Desglose diario.",
        response_model=ApiResponse[EnergySummary],
    )
    async def month(  # pyright: ignore[reportUnusedFunction]
        repo: RepoDep, settings: SettingsDep, _user: CurrentUser, device_id: str | None = None
    ) -> ApiResponse[EnergySummary]:
        return await _summary("month", repo, settings, device_id)

    @router.get(
        "/year",
        summary=f"{noun} del año",
        description=(
            f"{noun} desde el 1 de enero hasta ahora. Desglose mensual "
            "(totales exactos por mes calendario, no ventanas de duración fija)."
        ),
        response_model=ApiResponse[EnergySummary],
    )
    async def year(  # pyright: ignore[reportUnusedFunction]
        repo: RepoDep, settings: SettingsDep, _user: CurrentUser, device_id: str | None = None
    ) -> ApiResponse[EnergySummary]:
        return await _summary("year", repo, settings, device_id)

    return router
