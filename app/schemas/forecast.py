"""Modelos de /forecast."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ForecastPoint(BaseModel):
    """La energía esperada de una hora futura, con su banda."""

    time: datetime
    kwh: float
    p10: float
    p90: float


class ForecastBacktest(BaseModel):
    """El error del método, medido contra el ingenuo "lo mismo que hace 24 h".

    Se calcula prediciendo días que NO entraron en el entrenamiento: medir el
    error sobre los mismos datos con los que se calculó la media da un
    resultado bonito y falso. Publicarlo junto al pronóstico es lo que permite
    cambiar de método sin discutir de opiniones.
    """

    hours: int
    mae_kwh: float
    naive_mae_kwh: float


class PowerForecast(BaseModel):
    """Cuánta energía se espera importar en cada una de las próximas horas.

    `target` es siempre `import_kwh`: con generación, la potencia neta mezcla
    consumo y sol en un solo número y su pronóstico no se lee como "cuánto voy
    a gastar". La importada es lo que se paga y existe igual en una sede de
    consumo puro.

    Con `method: "insufficient_history"` no hay puntos: hacen falta al menos
    dos semanas de historial horario.
    """

    device_id: str | None
    target: Literal["import_kwh"]
    horizon_hours: int
    method: Literal["ewma_por_tipo_de_dia_y_hora", "insufficient_history"]
    points: list[ForecastPoint]
    backtest: ForecastBacktest | None


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
