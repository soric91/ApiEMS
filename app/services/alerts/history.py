"""Historial de anomalías: qué días se salieron de lo normal, y desde cuándo
el consumo cambió de nivel.

El panel ya avisa de la anomalía de ayer, pero esa alerta vive en memoria y se
pierde: no había forma de contestar "¿qué pasó el martes pasado?". Acá el
historial se RECALCULA sobre los datos guardados en vez de leerse de una tabla
de eventos.

Recalcular y no persistir es una decisión, no una comodidad:

- Los datos que deciden la anomalía (energía diaria y su banda por día de
  semana) ya están en InfluxDB. Guardar además el veredicto sería un segundo
  origen de verdad que puede contradecir al primero cuando la banda cambie.
- ApiEMS hoy solo LEE de InfluxDB. Abrir un camino de escritura para guardar
  eventos derivados es superficie nueva —credenciales, reintentos, retención—
  a cambio de nada que no se pueda derivar.
- El cálculo es una consulta de serie diaria más una banda ya cacheada.

Dos cosas distintas se reportan:

1. **Anomalías puntuales** — un día concreto fuera de su banda [p10, p90]. La
   frase la arma el mismo `daily_alert` que usa la alerta en vivo, para que el
   panel y el historial digan lo mismo del mismo día.
2. **Cambio de nivel** — el consumo que sube (o baja) y se QUEDA ahí, que las
   bandas puntuales no ven: cada día por separado puede caer dentro de lo
   normal mientras el nivel se corrió. Se busca el punto de corte que más
   separa el consumo típico de antes del de después, medido en unidades del
   ruido normal de la serie.
"""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.alerts import Alert, AlertsHistory, LevelShift
from app.schemas.influx import EnergyPoint
from app.services.alerts.detector import daily_alert
from app.services.analytics.anomaly import weekday_total_baseline
from app.utils.period import start_of_day

_DAY = timedelta(days=1)
# Mínimo de valores para poder mirar la variación de un día al siguiente.
_MIN_PARA_DIFERENCIAS = 2
# Mínimo de días para buscar un cambio de nivel: con menos, cualquier fin de
# semana parece un cambio permanente.
MIN_DAYS_FOR_SHIFT = 14
# Días que tienen que quedar a cada lado del corte para creerle: un cambio en
# el penúltimo día todavía no se sostuvo.
MIN_SIDE_DAYS = 5
# Cuántas veces el ruido normal tiene que separar los dos tramos para creerle
# al corte. Tres es el criterio de siempre en control de procesos: por debajo,
# la diferencia se explica por la variación de un día a otro.
SHIFT_SIGMAS = 3.0
# Por debajo de esto el cambio es real pero no vale la pena contarlo.
MIN_SHIFT_PCT = 10.0

_WEEKDAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTH_NAMES = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


def _mediana(valores: list[float]) -> float:
    ordenados = sorted(valores)
    medio = len(ordenados) // 2
    if len(ordenados) % 2 == 1:
        return ordenados[medio]
    return (ordenados[medio - 1] + ordenados[medio]) / 2


def _desviacion_robusta(valores: list[float]) -> float:
    """Escala a partir de la variación DE UN DÍA AL SIGUIENTE.

    No del desvío estándar ni de la MAD alrededor de la mediana: las dos miden
    la dispersión de TODA la serie, y un cambio de nivel infla esa dispersión
    él mismo. Con esa escala, el umbral crece tanto como la señal que se busca
    y el salto se esconde detrás de su propio efecto.

    La variación de un día al siguiente no la mueve un escalón —solo uno de los
    saltos entre días es grande, y la mediana lo ignora—, así que mide el ruido
    normal, que es contra lo que hay que comparar. El 1.4826 lleva la mediana
    absoluta a escala de desvío estándar, y el √2 corrige que la diferencia
    entre dos días tiene el doble de varianza que un día suelto."""
    if len(valores) < _MIN_PARA_DIFERENCIAS:
        return 0.0
    diferencias = [abs(valores[i] - valores[i - 1]) for i in range(1, len(valores))]
    return _mediana(diferencias) * 1.4826 / 2**0.5


def detect_level_shift(points: list[EnergyPoint]) -> tuple[int, float, float] | None:
    """(índice del cambio, consumo típico antes, consumo típico después).

    Se prueba cada día como posible punto de corte y se elige el que más
    separa el consumo típico de un lado del del otro, medido en unidades del
    ruido normal (`_desviacion_robusta`). Si ni el mejor corte separa lo
    suficiente, no hay cambio de nivel: la serie solo estaba oscilando.

    Medianas y no promedios a los dos lados: un solo día atípico —una fiesta,
    un corte de luz— mueve el promedio de su lado lo bastante para disfrazarse
    de cambio permanente. La mediana solo se mueve si se movió la mayoría de
    los días, que es exactamente la condición de un cambio sostenido.
    """
    if len(points) < MIN_DAYS_FOR_SHIFT:
        return None

    valores = [p.value for p in points]
    # Piso de ruido: una serie sin variación medible (o con la mitad de los
    # días idénticos) daría sigma cero y cualquier diferencia parecería
    # infinitas veces el ruido. El 1% del nivel típico es un piso conservador —
    # con él, un cambio tiene que ser de al menos un 3% para reportarse.
    sigma = max(_desviacion_robusta(valores), abs(_mediana(valores)) * 0.01)
    if sigma <= 0:
        return None

    mejor: tuple[float, float, int, float, float] | None = None
    for corte in range(MIN_SIDE_DAYS, len(valores) - MIN_SIDE_DAYS + 1):
        antes = _mediana(valores[:corte])
        despues = _mediana(valores[corte:])
        separacion = abs(despues - antes) / sigma
        # Desempate: el salto real entre el día anterior y el del corte. Con un
        # escalón limpio, varios cortes separan las medianas exactamente igual
        # (todos los de un lado valen lo mismo); el que hay que reportar es
        # aquel en que el consumo cambió, no el primero que empata.
        salto = abs(valores[corte] - valores[corte - 1])
        candidato = (separacion, salto, corte, antes, despues)
        if mejor is None or candidato[:2] > mejor[:2]:
            mejor = candidato

    if mejor is None or mejor[0] < SHIFT_SIGMAS:
        return None
    _, _, corte, antes, despues = mejor
    return corte, antes, despues


def _fecha_larga(dt: datetime, tz_name: str) -> str:
    local = dt.astimezone(ZoneInfo(tz_name))
    return f"{local.day} de {_MONTH_NAMES[local.month - 1]}"


def _level_shift(points: list[EnergyPoint], tz_name: str) -> LevelShift | None:
    detectado = detect_level_shift(points)
    if detectado is None:
        return None
    corte, antes, despues = detectado
    if antes <= 0:
        return None
    delta_pct = (despues - antes) / antes * 100
    if abs(delta_pct) < MIN_SHIFT_PCT:
        return None

    subio = delta_pct > 0
    fecha = points[corte].time
    return LevelShift(
        detected_at=fecha,
        before_kwh=round(antes, 2),
        after_kwh=round(despues, 2),
        delta_pct=round(delta_pct, 1),
        direction="up" if subio else "down",
        message=(
            f"Desde el {_fecha_larga(fecha, tz_name)} tu consumo diario "
            f"{'subió' if subio else 'bajó'} de {antes:.1f} a {despues:.1f} kWh en un día "
            "típico, y se mantuvo ahí."
        ),
    )


async def alerts_history(
    repo: InfluxDataSource,
    settings: Settings,
    start: datetime,
    stop: datetime,
    device_id: str | None,
) -> AlertsHistory:
    """Qué días del rango se salieron de lo normal, y desde cuándo cambió el
    nivel de consumo.

    Solo días COMPLETOS: el día en curso siempre parecería bajo comparado con
    un día entero, que es la misma razón por la que la alerta en vivo evalúa
    ayer y no hoy.
    """
    tz_name = settings.TIMEZONE
    fin = min(stop, start_of_day(tz_name))
    if fin <= start:
        return AlertsHistory(
            device_id=device_id,
            period_start=start,
            period_end=stop,
            days_analyzed=0,
            anomalies=[],
            level_shift=None,
        )

    dias, bands = await asyncio.gather(
        repo.energy_series(Variable.POWER_ACTIVE_TOTAL_POS, start, fin, _DAY, device_id),
        weekday_total_baseline(repo, device_id, tz_name),
    )

    anomalias: list[Alert] = []
    for punto in dias:
        weekday = punto.time.astimezone(ZoneInfo(tz_name)).weekday()
        band = bands.get(weekday)
        if band is None:
            continue
        alerta = daily_alert(device_id, punto.time, weekday, punto.value, band)
        if alerta is not None:
            anomalias.append(alerta)

    return AlertsHistory(
        device_id=device_id,
        period_start=start,
        period_end=fin,
        days_analyzed=len(dias),
        # Más recientes primero: es el orden en que se lee una línea de tiempo.
        anomalies=sorted(anomalias, key=lambda a: a.timestamp, reverse=True),
        level_shift=_level_shift(dias, tz_name),
    )
