"""Modelos de /forecast."""

from typing import Literal

from pydantic import BaseModel


class BillForecast(BaseModel):
    """Cuánto va del mes y en cuánto termina si el consumo sigue como viene.

    `method` dice cómo se proyectó:

    - `ewma_por_tipo_de_dia` — media exponencial de los últimos 28 días,
      separando laborales, sábados y domingos.
    - `insufficient_history` — menos de 14 días completos de historial. Los
      campos proyectados vienen en `null` y el panel no proyecta: una media
      sacada de tres días dice más del azar que del consumo.

    La banda `p10`-`p90` sale de la dispersión real de cada tipo de día (los
    días buenos y malos que ya ocurrieron), no de una fórmula de error.
    """

    month: str
    device_id: str | None
    kwh_mtd: float
    export_mtd_kwh: float
    days_elapsed: float
    days_total: int
    kwh_projected: float | None
    kwh_p10: float | None
    kwh_p90: float | None
    export_projected_kwh: float | None
    cost_projected_cop: float | None
    cost_p10_cop: float | None
    cost_p90_cop: float | None
    method: Literal["ewma_por_tipo_de_dia", "insufficient_history"]
