"""Exportación (excedente solar hacia la red) — difference() sobre POWER_ACTIVE_TOTAL_NEG."""

from app.api.v1.energy_router_factory import build_energy_router
from app.models.variables import Variable

router = build_energy_router(
    prefix="/export",
    tag="Export",
    counter=Variable.POWER_ACTIVE_TOTAL_NEG,
    noun="Exportación",
)
