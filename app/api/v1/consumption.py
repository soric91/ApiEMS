"""Consumo (energía importada de la red) — difference() sobre POWER_ACTIVE_TOTAL_POS."""

from app.api.v1.energy_router_factory import build_energy_router
from app.models.variables import Variable

router = build_energy_router(
    prefix="/consumption",
    tag="Consumption",
    counter=Variable.POWER_ACTIVE_TOTAL_POS,
    noun="Consumo",
)
