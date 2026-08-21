"""Proyección de la factura del mes en curso.

"Vas en 142 kWh · $118.400. A este ritmo terminas el mes en ~$248.000." Es lo
que convierte el panel en algo que se abre cada semana en vez de cuando llega
el recibo — y lo trae cualquier plataforma de energía del mercado.

Cómo se proyecta, sin ML:

1. Se toman los kWh de cada día COMPLETO de los últimos 28 días.
2. Se separan por tipo de día (laboral / sábado / domingo o festivo): un lunes
   no se parece a un domingo, y promediarlos juntos da una media que no
   describe a ninguno de los dos.
3. Por cada tipo se calcula una media exponencial (EWMA, alfa 0.3): la semana
   pasada pesa más que la de hace un mes, que es lo que hace que la proyección
   reaccione a una mudanza o a un aire acondicionado nuevo.
4. Los kWh proyectados son lo que va del mes MÁS la media de cada día que
   falta, incluida la fracción que le queda al día de hoy.
5. La banda p10-p90 sale de la dispersión real de cada tipo de día, no de una
   fórmula: son los días buenos y malos que ya ocurrieron.

El costo NO se recalcula acá: se arma un punto de energía por mes y se pasa
por `compute_cost_from_points`, el mismo motor que factura /costs y /reports.
Duplicar la lógica de los dos tramos del excedente sería tener dos respuestas
distintas a "cuánto cuesta este mes".

Sin al menos 14 días completos de historial no se proyecta nada: una media
sacada de tres días dice más del azar que del consumo.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.forecast import BillForecast
from app.schemas.influx import EnergyPoint
from app.schemas.tariff import TariffConfig
from app.services.influx.cache import cached_energy_series, cached_energy_total
from app.services.tariff.cost import compute_cost_from_points
from app.utils.period import start_of_day, start_of_month

# Cuánto historial se mira para la media. Cuatro semanas dan al menos cuatro
# muestras de cada tipo de día sin arrastrar la estación pasada.
LOOKBACK_DAYS = 28
# Por debajo de esto no se proyecta: la media diría más del azar que del
# consumo.
MIN_HISTORY_DAYS = 14
# Cuánto pesa lo reciente. 0.3 da media vida de ~2 días de ese tipo: reacciona
# a un cambio real sin bailar con un día atípico.
EWMA_ALPHA = 0.3
_DAY = timedelta(days=1)
_SABADO = 5
_DOMINGO = 6
_DICIEMBRE = 12


def _tipo_de_dia(fecha: datetime, tz_name: str) -> str:
    """laboral / sabado / domingo, en hora local.

    Los festivos no se distinguen del domingo: el calendario de festivos
    colombiano no vive en este sistema, y meterlo mal sería peor que tratarlos
    como el día de baja actividad que suelen ser.
    """
    weekday = fecha.astimezone(ZoneInfo(tz_name)).weekday()
    if weekday == _SABADO:
        return "sabado"
    if weekday == _DOMINGO:
        return "domingo"
    return "laboral"


def _ewma(valores: list[float], alpha: float = EWMA_ALPHA) -> float:
    """Media exponencial; el último valor es el que más pesa."""
    media = valores[0]
    for valor in valores[1:]:
        media = alpha * valor + (1 - alpha) * media
    return media


def _percentil(valores: list[float], q: float) -> float:
    """Percentil por posición sobre los valores observados (sin interpolar).

    Los días son pocos (cuatro o cinco por tipo): interpolar entre dos de ellos
    inventa un día que no pasó.
    """
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, max(0, round(q * (len(ordenados) - 1))))
    return ordenados[indice]


def _por_tipo(points: list[EnergyPoint], tz_name: str) -> dict[str, list[float]]:
    agrupado: dict[str, list[float]] = {}
    for point in points:
        agrupado.setdefault(_tipo_de_dia(point.time, tz_name), []).append(point.value)
    return agrupado


def _dias_restantes(now: datetime, month_end: datetime, tz_name: str) -> list[datetime]:
    """Los días que faltan del mes, desde mañana hasta el último."""
    dia = start_of_day(tz_name, now) + _DAY
    restantes: list[datetime] = []
    while dia < month_end:
        restantes.append(dia)
        dia += _DAY
    return restantes


def _fraccion_restante_de_hoy(now: datetime, tz_name: str) -> float:
    transcurrido = (now - start_of_day(tz_name, now)) / _DAY
    return max(0.0, 1.0 - transcurrido)


def _proyectar(
    mtd: float,
    por_tipo: dict[str, list[float]],
    now: datetime,
    month_end: datetime,
    tz_name: str,
) -> tuple[float, float, float]:
    """(proyectado, p10, p90) en kWh, sumando lo que falta del mes a lo que va."""
    medias = {tipo: _ewma(valores) for tipo, valores in por_tipo.items()}
    bajos = {tipo: _percentil(valores, 0.10) for tipo, valores in por_tipo.items()}
    altos = {tipo: _percentil(valores, 0.90) for tipo, valores in por_tipo.items()}
    # Un tipo sin muestras (un mes que empieza en domingo y todavía no tuvo
    # ninguno) cae al promedio de lo que sí hay: es mejor que descartar el día.
    respaldo = sum(medias.values()) / len(medias)

    def suma(tabla: dict[str, float], defecto: float) -> float:
        total = sum(tabla.get(_tipo_de_dia(dia, tz_name), defecto) for dia in restantes)
        hoy = tabla.get(_tipo_de_dia(now, tz_name), defecto)
        return total + hoy * _fraccion_restante_de_hoy(now, tz_name)

    restantes = _dias_restantes(now, month_end, tz_name)
    return (
        round(mtd + suma(medias, respaldo), 2),
        round(mtd + suma(bajos, respaldo), 2),
        round(mtd + suma(altos, respaldo), 2),
    )


def _costo(
    tariff: TariffConfig,
    month_start: datetime,
    month_end: datetime,
    device_id: str | None,
    consumo_kwh: float,
    export_kwh: float,
) -> float:
    """Costo neto del mes proyectado, con el motor de tarifa de siempre.

    Se arma UN punto de energía en el mes: `compute_cost_from_points` agrega por
    mes calendario, así que un solo punto con el total proyectado produce
    exactamente el mismo reparto de tramos que produciría la serie completa.
    """
    punto_consumo = [EnergyPoint(time=month_start, value=consumo_kwh)]
    punto_export = [EnergyPoint(time=month_start, value=export_kwh)]
    desglose = compute_cost_from_points(
        tariff,
        "month",
        month_start,
        month_end,
        device_id,
        punto_consumo,
        punto_export,
        consumo_kwh,
        export_kwh,
    )
    return desglose.net_cost_cop


async def bill_forecast(
    repo: InfluxDataSource,
    settings: Settings,
    tariff: TariffConfig,
    device_id: str | None,
    now: datetime | None = None,
) -> BillForecast:
    """Cuánto va del mes y en cuánto termina si el consumo sigue como viene."""
    reference = now or datetime.now(tz=UTC)
    tz_name = settings.TIMEZONE
    month_start = start_of_month(tz_name, reference)
    today_start = start_of_day(tz_name, reference)
    month_end = _fin_de_mes(month_start, tz_name)
    history_start = today_start - timedelta(days=LOOKBACK_DAYS)

    consumo_mtd, export_mtd, dias_consumo, dias_export = await asyncio.gather(
        cached_energy_total(
            repo, Variable.POWER_ACTIVE_TOTAL_POS, month_start, reference, device_id
        ),
        cached_energy_total(
            repo, Variable.POWER_ACTIVE_TOTAL_NEG, month_start, reference, device_id
        ),
        cached_energy_series(
            repo, Variable.POWER_ACTIVE_TOTAL_POS, history_start, today_start, _DAY, device_id
        ),
        cached_energy_series(
            repo, Variable.POWER_ACTIVE_TOTAL_NEG, history_start, today_start, _DAY, device_id
        ),
    )

    dias_totales = round((month_end - month_start) / _DAY)
    dias_transcurridos = round((reference - month_start) / _DAY, 2)
    base = BillForecast(
        month=f"{month_start.astimezone(ZoneInfo(tz_name)):%Y-%m}",
        device_id=device_id,
        kwh_mtd=round(consumo_mtd, 2),
        export_mtd_kwh=round(export_mtd, 2),
        days_elapsed=dias_transcurridos,
        days_total=dias_totales,
        kwh_projected=None,
        kwh_p10=None,
        kwh_p90=None,
        export_projected_kwh=None,
        cost_projected_cop=None,
        cost_p10_cop=None,
        cost_p90_cop=None,
        method="insufficient_history",
    )
    if len(dias_consumo) < MIN_HISTORY_DAYS:
        return base

    consumo_por_tipo = _por_tipo(dias_consumo, tz_name)
    proyectado, p10, p90 = _proyectar(consumo_mtd, consumo_por_tipo, reference, month_end, tz_name)
    export_por_tipo = _por_tipo(dias_export, tz_name)
    export_proyectado = (
        _proyectar(export_mtd, export_por_tipo, reference, month_end, tz_name)[0]
        if export_por_tipo
        else round(export_mtd, 2)
    )

    return base.model_copy(
        update={
            "kwh_projected": proyectado,
            "kwh_p10": p10,
            "kwh_p90": p90,
            "export_projected_kwh": export_proyectado,
            "cost_projected_cop": _costo(
                tariff, month_start, month_end, device_id, proyectado, export_proyectado
            ),
            # La banda de costo usa la MISMA exportación proyectada: la
            # incertidumbre que se está mostrando es la del consumo.
            "cost_p10_cop": _costo(
                tariff, month_start, month_end, device_id, p10, export_proyectado
            ),
            "cost_p90_cop": _costo(
                tariff, month_start, month_end, device_id, p90, export_proyectado
            ),
            "method": "ewma_por_tipo_de_dia",
        }
    )


def _fin_de_mes(month_start: datetime, tz_name: str) -> datetime:
    """El primer instante del mes siguiente, en hora local."""
    local = month_start.astimezone(ZoneInfo(tz_name))
    year = local.year + (1 if local.month == _DICIEMBRE else 0)
    month = 1 if local.month == _DICIEMBRE else local.month + 1
    return datetime(year, month, 1, tzinfo=ZoneInfo(tz_name)).astimezone(UTC)
