"""Pronóstico de consumo por hora para las próximas horas.

Qué se predice: la ENERGÍA IMPORTADA de cada hora (kWh), no la potencia neta.
Con generación, la neta mezcla consumo y sol en un solo número y su pronóstico
no se puede leer como "cuánto voy a gastar"; la importada es lo que se paga y
existe igual en una sede de consumo puro, así que el mismo cálculo sirve para
toda la flota.

Cómo, sin modelo entrenado: cada hora futura se predice con la media
exponencial de esa MISMA hora en los días del mismo tipo (laboral / sábado /
domingo). Es el "seasonal naive" con memoria, y es casi todo lo que hay que
saber acá: en el modelo entrenado de energyML, el retardo de 24 h pesa 0.72 de
la importancia total. La banda p10-p90 sale de la dispersión real de esa misma
hora en esos mismos días — los valores que ya ocurrieron, no una fórmula de
error.

Cada respuesta trae su propio backtest: se predicen los últimos días con datos
anteriores a ellos y se compara el error contra el ingenuo "lo mismo que hace
24 h". Publicar el error medido junto al pronóstico es lo que permite cambiar
de método (o traer el modelo de energyML) sin discutir de opiniones.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.forecast import ForecastBacktest, ForecastPoint, PowerForecast
from app.schemas.influx import EnergyPoint
from app.services.influx.cache import cached_energy_series

# Cuánto histórico alimenta la media. Cuatro semanas dan cuatro muestras de
# cada (tipo de día, hora) sin arrastrar la estación pasada.
LOOKBACK_DAYS = 28
# Mínimo de días completos para pronosticar algo.
MIN_HISTORY_DAYS = 14
# Muestras mínimas de un bucket (tipo de día, hora) para creerle.
MIN_BUCKET_SAMPLES = 2
# Cuánto pesa lo reciente, igual que en la proyección de la factura.
EWMA_ALPHA = 0.3
DEFAULT_HORIZON_HOURS = 48
MAX_HORIZON_HOURS = 168
# Cuántos días se reservan para medir el error del método contra el ingenuo.
BACKTEST_DAYS = 7

_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)
_SABADO = 5
_DOMINGO = 6


def _tipo_de_dia(momento: datetime, tz_name: str) -> str:
    weekday = momento.astimezone(ZoneInfo(tz_name)).weekday()
    if weekday == _SABADO:
        return "sabado"
    if weekday == _DOMINGO:
        return "domingo"
    return "laboral"


def _clave(momento: datetime, tz_name: str) -> tuple[str, int]:
    local = momento.astimezone(ZoneInfo(tz_name))
    return _tipo_de_dia(momento, tz_name), local.hour


def _ewma(valores: list[float]) -> float:
    media = valores[0]
    for valor in valores[1:]:
        media = EWMA_ALPHA * valor + (1 - EWMA_ALPHA) * media
    return media


def _percentil(valores: list[float], q: float) -> float:
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, max(0, round(q * (len(ordenados) - 1))))
    return ordenados[indice]


def _buckets(points: list[EnergyPoint], tz_name: str) -> dict[tuple[str, int], list[float]]:
    """Los kWh de cada (tipo de día, hora local), en orden cronológico."""
    agrupado: dict[tuple[str, int], list[float]] = {}
    for punto in points:
        agrupado.setdefault(_clave(punto.time, tz_name), []).append(punto.value)
    return agrupado


def _prediccion(
    buckets: dict[tuple[str, int], list[float]], momento: datetime, tz_name: str
) -> tuple[float, float, float] | None:
    """(esperado, p10, p90) para una hora futura, o None si no hay muestras."""
    valores = buckets.get(_clave(momento, tz_name))
    if valores is None or len(valores) < MIN_BUCKET_SAMPLES:
        return None
    return _ewma(valores), _percentil(valores, 0.10), _percentil(valores, 0.90)


def _backtest(
    points: list[EnergyPoint], tz_name: str, dias: int = BACKTEST_DAYS
) -> ForecastBacktest | None:
    """Error del método contra el ingenuo "lo mismo que hace 24 h".

    Se entrena con lo anterior a la ventana de prueba y se predicen sus horas,
    que es la única forma de que el número signifique algo: medir el error
    sobre los mismos datos con los que se calculó la media siempre da un
    resultado bonito y falso.
    """
    if len(points) < dias * 24 * 2:
        return None

    corte = len(points) - dias * 24
    entrenamiento, prueba = points[:corte], points[corte:]
    buckets = _buckets(entrenamiento, tz_name)
    por_tiempo = {p.time: p.value for p in points}

    errores: list[float] = []
    errores_ingenuo: list[float] = []
    for punto in prueba:
        estimado = _prediccion(buckets, punto.time, tz_name)
        ayer = por_tiempo.get(punto.time - _DAY)
        if estimado is None or ayer is None:
            continue
        errores.append(abs(estimado[0] - punto.value))
        errores_ingenuo.append(abs(ayer - punto.value))

    if not errores:
        return None
    return ForecastBacktest(
        hours=len(errores),
        mae_kwh=round(sum(errores) / len(errores), 4),
        naive_mae_kwh=round(sum(errores_ingenuo) / len(errores_ingenuo), 4),
    )


async def power_forecast(
    repo: InfluxDataSource,
    settings: Settings,
    device_id: str | None,
    horizon_hours: int = DEFAULT_HORIZON_HOURS,
    now: datetime | None = None,
) -> PowerForecast:
    """Cuánta energía se espera importar en cada una de las próximas horas."""
    reference = now or datetime.now(tz=UTC)
    tz_name = settings.TIMEZONE
    # Se arranca en la próxima hora en punto: la actual está a medio consumir y
    # pronosticarla entera daría un número que ya no se puede cumplir.
    inicio = reference.replace(minute=0, second=0, microsecond=0) + _HOUR
    historia_desde = inicio - timedelta(days=LOOKBACK_DAYS)

    points = await cached_energy_series(
        repo, Variable.POWER_ACTIVE_TOTAL_POS, historia_desde, reference, _HOUR, device_id
    )

    vacio = PowerForecast(
        device_id=device_id,
        target="import_kwh",
        horizon_hours=horizon_hours,
        method="insufficient_history",
        points=[],
        backtest=None,
    )
    if len(points) < MIN_HISTORY_DAYS * 24:
        return vacio

    buckets, backtest = await asyncio.gather(
        asyncio.to_thread(_buckets, points, tz_name),
        asyncio.to_thread(_backtest, points, tz_name),
    )

    predichos: list[ForecastPoint] = []
    for adelanto in range(horizon_hours):
        momento = inicio + adelanto * _HOUR
        estimado = _prediccion(buckets, momento, tz_name)
        if estimado is None:
            continue
        esperado, p10, p90 = estimado
        predichos.append(
            ForecastPoint(
                time=momento,
                kwh=round(esperado, 3),
                p10=round(p10, 3),
                p90=round(p90, 3),
            )
        )

    if not predichos:
        return vacio

    return PowerForecast(
        device_id=device_id,
        target="import_kwh",
        horizon_hours=horizon_hours,
        method="ewma_por_tipo_de_dia_y_hora",
        points=predichos,
        backtest=backtest,
    )
