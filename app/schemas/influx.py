"""Modelos de resultados de consultas a InfluxDB."""

from datetime import datetime

from pydantic import BaseModel


class TimeSeriesPoint(BaseModel):
    time: datetime
    value: float


class EnergyPoint(BaseModel):
    """Energía (kWh) consumida/exportada en la ventana que termina en `time`."""

    time: datetime
    value: float
