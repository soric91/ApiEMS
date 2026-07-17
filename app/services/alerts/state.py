"""Historial de alertas recientes, en memoria (mismo patrón que RealtimeState).

Se pierde al reiniciar el proceso — igual que el resto del estado en RAM del
proyecto. Si en el futuro hace falta persistencia entre reinicios, es el
punto a extender (ej. escribir a InfluxDB como measurement nuevo).
"""

from collections import deque

from app.schemas.alerts import Alert

MAX_RECENT = 200


class AlertsState:
    def __init__(self) -> None:
        self._recent: deque[Alert] = deque(maxlen=MAX_RECENT)

    def add(self, alert: Alert) -> None:
        self._recent.append(alert)

    def recent(self, limit: int = 50) -> list[Alert]:
        return list(self._recent)[-limit:][::-1]  # más reciente primero
