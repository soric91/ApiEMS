"""Historial de alertas recientes, en memoria (mismo patrón que RealtimeState).

Se pierde al reiniciar el proceso — igual que el resto del estado en RAM del
proyecto. Si en el futuro hace falta persistencia entre reinicios, es el
punto a extender (ej. escribir a InfluxDB como measurement nuevo).
"""

from collections import deque
from datetime import datetime, timedelta

from app.schemas.alerts import Alert, AlertKind, AlertSeverity

MAX_RECENT = 200

# check_hourly() se evalúa en CADA mensaje MQTT (~cada 30-60s); mientras una
# anomalía persiste (ej. sistema solar apagado 2h), sin esto se dispara una
# alerta idéntica por cada mensaje. Cooldown por (device, hora, tipo) evita
# el spam sin perder la detección instantánea del primer mensaje anómalo.
ALERT_COOLDOWN = timedelta(minutes=15)

_AlertKey = tuple[str | None, int, AlertKind]


class AlertsState:
    def __init__(self, cooldown: timedelta = ALERT_COOLDOWN) -> None:
        self._recent: deque[Alert] = deque(maxlen=MAX_RECENT)
        self._cooldown = cooldown
        self._last_emitted: dict[_AlertKey, tuple[datetime, AlertSeverity]] = {}

    def add_if_due(self, alert: Alert) -> bool:
        """Registra la alerta (historial + WS) solo si no es una repetición
        de la misma condición dentro de la ventana de cooldown. Una
        severidad que empeora (`moderate` -> `high`) siempre se emite de
        inmediato, sin esperar el cooldown — es información nueva, no ruido.
        Devuelve True si la alerta debe emitirse."""
        key: _AlertKey = (alert.device_id, alert.bucket, alert.kind)
        last = self._last_emitted.get(key)
        if last is not None:
            last_time, last_severity = last
            escalated = last_severity == "moderate" and alert.severity == "high"
            if alert.timestamp - last_time < self._cooldown and not escalated:
                return False
        self._last_emitted[key] = (alert.timestamp, alert.severity)
        self._recent.append(alert)
        return True

    def recent(self, limit: int = 50) -> list[Alert]:
        return list(self._recent)[-limit:][::-1]  # más reciente primero
