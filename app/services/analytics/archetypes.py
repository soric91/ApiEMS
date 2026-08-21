"""Arquetipos de día: qué "tipos de día" tiene realmente esta instalación.

El perfil horario promedio mezcla todo en una sola curva: un laboral con
horario de oficina, un domingo vacío y el día que se hizo un asado terminan
promediados en algo que no describe a ninguno. Acá se agrupan los días por la
FORMA de su consumo y se muestra cada grupo por separado — "tienes tres tipos
de día" es una frase que el cliente reconoce en su propia vida.

Cómo:

1. Cada día es un vector de 24 números: la fracción de la energía del día que
   se consumió en cada hora. Fracción y no kWh: así dos días con la misma
   forma pero distinta magnitud (un martes fresco y uno caluroso) caen en el
   mismo grupo, que es lo que se está buscando.
2. La energía es siempre la IMPORTADA por hora (`difference` del contador), no
   la potencia neta: la neta puede ser negativa con generación y las
   fracciones dejarían de tener sentido. Con esto, el mismo cálculo sirve para
   una sede con solar y para una de consumo puro.
3. K-means con k entre 2 y 5, eligiendo k por silueta. La silueta mide qué tan
   separados quedaron los grupos: si el mejor k no llega a un mínimo, se
   informa que esta instalación NO tiene tipos de día distinguibles, en vez de
   partir en dos una nube que es una sola.

El k-means arranca con inicialización determinista (el día más lejano al ya
elegido, no uno al azar): dos consultas seguidas tienen que devolver los
mismos grupos, o el cliente vería su "tipo de día" cambiar al recargar.
"""

import asyncio
from datetime import datetime, timedelta

import polars as pl

from app.core.cache import cached
from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import DayArchetype, DayArchetypesResult, DayAssignment
from app.schemas.influx import EnergyPoint
from app.utils.period import start_of_day

_HOURS = 24
_HOUR = timedelta(hours=1)
# Horas con dato que necesita un día para entrar: con menos, su "forma" es la
# de las horas que faltaron, no la del consumo.
MIN_HOURS_PER_DAY = 20
# Días mínimos para intentar agrupar. Con menos, cualquier partición parece
# buena y ninguna significa nada.
MIN_DAYS = 21
# Cuántos grupos se prueban.
K_RANGE = (2, 3, 4, 5)
# Silueta mínima para creerle a la partición. Por debajo de 0.25 los grupos se
# tocan tanto que separarlos es dibujar una frontera donde no hay ninguna.
MIN_SILHOUETTE = 0.25
# Iteraciones del k-means. Con 90 días de 24 dimensiones converge muy antes.
_MAX_ITERATIONS = 50
# Sin al menos dos grupos no hay nada que separar: la silueta no está definida.
_MIN_GRUPOS = 2
ARCHETYPES_TTL = 86400
# Cuánto histórico se agrupa por defecto: un trimestre da varias muestras de
# cada día de semana sin arrastrar la estación pasada.
DEFAULT_DAYS = 90

_WEEKDAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_WEEKEND = {5, 6}
# Desde qué proporción de días de un grupo se lo llama laboral o de fin de
# semana. Por debajo, el grupo mezcla los dos y se nombra por su nivel.
_LABEL_MAJORITY = 0.7


def _vectores(
    points: list[EnergyPoint], tz_name: str
) -> tuple[list[str], list[list[float]], list[float], list[int]]:
    """(fechas, formas, kWh del día, día de semana) — cálculo puro (Polars).

    La "forma" es la fracción de la energía diaria consumida en cada hora, que
    es lo que se agrupa: dos martes con el mismo horario y distinto calor tienen
    la misma forma y distinto total.
    """
    frame = pl.DataFrame(
        {"time": [p.time for p in points], "kwh": [p.value for p in points]},
        schema={"time": pl.Datetime(time_zone="UTC"), "kwh": pl.Float64},
    ).with_columns(  # pyright: ignore[reportUnknownMemberType]
        pl.col("time").dt.convert_time_zone(tz_name).dt.date().alias("fecha"),
        pl.col("time").dt.convert_time_zone(tz_name).dt.hour().alias("hora"),
    )

    por_dia: dict[str, list[float | None]] = {}
    for row in frame.iter_rows(named=True):
        fecha = str(row["fecha"])
        horas = por_dia.setdefault(fecha, [None] * _HOURS)
        horas[int(row["hora"])] = float(row["kwh"])

    fechas: list[str] = []
    formas: list[list[float]] = []
    totales: list[float] = []
    dias_semana: list[int] = []
    for fecha in sorted(por_dia):
        horas = por_dia[fecha]
        con_dato = [h for h in horas if h is not None]
        total = sum(con_dato)
        if len(con_dato) < MIN_HOURS_PER_DAY or total <= 0:
            continue
        fechas.append(fecha)
        formas.append([(h or 0.0) / total for h in horas])
        totales.append(round(total, 2))
        dias_semana.append(datetime.fromisoformat(fecha).weekday())
    return fechas, formas, totales, dias_semana


def _distancia(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) ** 0.5


def _semillas(vectores: list[list[float]], k: int) -> list[list[float]]:
    """Inicialización determinista: el primer día, y después el más lejano a
    lo ya elegido.

    Sin azar a propósito. Con semillas aleatorias, dos consultas seguidas
    pueden devolver grupos distintos, y un cliente que recarga la página vería
    su "tipo de día" cambiar sin que cambiara nada."""
    centros = [vectores[0]]
    while len(centros) < k:
        siguiente = max(vectores, key=lambda v: min(_distancia(v, centro) for centro in centros))
        centros.append(siguiente)
    return centros


def _kmeans(vectores: list[list[float]], k: int) -> list[int]:
    """Etiqueta de grupo para cada vector."""
    centros = _semillas(vectores, k)
    etiquetas = [0] * len(vectores)
    for _ in range(_MAX_ITERATIONS):
        nuevas = [
            min(range(k), key=lambda c: _distancia(vector, centros[c])) for vector in vectores
        ]
        if nuevas == etiquetas:
            break
        etiquetas = nuevas
        for c in range(k):
            miembros = [v for v, e in zip(vectores, etiquetas, strict=True) if e == c]
            if miembros:
                centros[c] = [sum(dim) / len(miembros) for dim in zip(*miembros, strict=True)]
    return etiquetas


def _silueta(vectores: list[list[float]], etiquetas: list[int]) -> float:
    """Qué tan separados quedaron los grupos, de -1 a 1.

    Para cada día: qué tan cerca está de los suyos comparado con el grupo
    ajeno más cercano. Es lo que decide cuántos tipos de día tiene esta
    instalación, en vez de fijar un número a mano."""
    grupos: dict[int, list[list[float]]] = {}
    for vector, etiqueta in zip(vectores, etiquetas, strict=True):
        grupos.setdefault(etiqueta, []).append(vector)
    if len(grupos) < _MIN_GRUPOS:
        return -1.0

    siluetas: list[float] = []
    for vector, etiqueta in zip(vectores, etiquetas, strict=True):
        propios = [v for v in grupos[etiqueta] if v is not vector]
        if not propios:
            continue
        a = sum(_distancia(vector, v) for v in propios) / len(propios)
        b = min(
            sum(_distancia(vector, v) for v in miembros) / len(miembros)
            for otro, miembros in grupos.items()
            if otro != etiqueta
        )
        mayor = max(a, b)
        siluetas.append(0.0 if mayor == 0 else (b - a) / mayor)
    return sum(siluetas) / len(siluetas) if siluetas else -1.0


def _etiqueta(dias_semana: list[int], total_dias: int) -> str:
    """Cómo se llama un grupo, según de qué días está hecho."""
    if not dias_semana:
        return "Sin días"
    fin_de_semana = sum(1 for d in dias_semana if d in _WEEKEND) / len(dias_semana)
    if fin_de_semana >= _LABEL_MAJORITY:
        return "Fin de semana"
    if fin_de_semana <= 1 - _LABEL_MAJORITY:
        return "Laboral"
    # Un grupo chico que mezcla días de semana y de fin de semana es "lo que
    # se sale del patrón", no un tercer tipo de rutina.
    if len(dias_semana) <= max(2, total_dias // 10):
        return "Atípico"
    return "Mixto"


def _agrupar(
    fechas: list[str],
    formas: list[list[float]],
    totales: list[float],
    dias_semana: list[int],
) -> tuple[list[DayArchetype], list[DayAssignment], float]:
    mejor: tuple[float, int, list[int]] | None = None
    for k in K_RANGE:
        if k >= len(formas):
            continue
        etiquetas = _kmeans(formas, k)
        puntaje = _silueta(formas, etiquetas)
        if mejor is None or puntaje > mejor[0]:
            mejor = (puntaje, k, etiquetas)

    if mejor is None:
        return [], [], -1.0
    puntaje, k, etiquetas = mejor

    por_grupo: list[tuple[int, DayArchetype]] = []
    for grupo in range(k):
        indices = [i for i, e in enumerate(etiquetas) if e == grupo]
        if not indices:
            continue
        curva = [
            round(sum(formas[i][hora] for i in indices) / len(indices), 4) for hora in range(_HOURS)
        ]
        dias_del_grupo = [dias_semana[i] for i in indices]
        por_grupo.append(
            (
                grupo,
                DayArchetype(
                    label=_etiqueta(dias_del_grupo, len(formas)),
                    day_count=len(indices),
                    avg_kwh=round(sum(totales[i] for i in indices) / len(indices), 2),
                    hourly_share=curva,
                    weekdays=sorted({_WEEKDAY_NAMES[d] for d in dias_del_grupo}),
                ),
            )
        )

    # De más consumo a menos: el grupo que más pesa es el que hay que mirar
    # primero. El índice que se publica es el de ESTE orden, no el interno del
    # k-means, que no significa nada.
    por_grupo.sort(key=lambda par: par[1].avg_kwh, reverse=True)
    indice_publico = {grupo: i for i, (grupo, _) in enumerate(por_grupo)}
    asignaciones = [
        DayAssignment(date=fecha, archetype=indice_publico[etiqueta], kwh=totales[i])
        for i, (fecha, etiqueta) in enumerate(zip(fechas, etiquetas, strict=True))
    ]
    return [arquetipo for _, arquetipo in por_grupo], asignaciones, puntaje


@cached(ttl_seconds=ARCHETYPES_TTL)
async def day_archetypes(
    repo: InfluxDataSource,
    device_id: str | None,
    tz_name: str,
    days: int = DEFAULT_DAYS,
) -> DayArchetypesResult:
    """Los tipos de día de esta instalación, agrupados por la forma de su
    consumo horario. Cacheado 24 h: no cambian entre dos cargas del panel."""
    stop = start_of_day(tz_name)
    start = stop - timedelta(days=days)
    points = await repo.energy_series(
        Variable.POWER_ACTIVE_TOTAL_POS, start, stop, _HOUR, device_id
    )

    vacio = DayArchetypesResult(
        device_id=device_id,
        period_start=start,
        period_end=stop,
        days_analyzed=0,
        silhouette=None,
        archetypes=[],
        assignments=[],
    )
    if not points:
        return vacio

    fechas, formas, totales, dias_semana = await asyncio.to_thread(_vectores, points, tz_name)
    if len(formas) < MIN_DAYS:
        return vacio.model_copy(update={"days_analyzed": len(formas)})

    arquetipos, asignaciones, puntaje = await asyncio.to_thread(
        _agrupar, fechas, formas, totales, dias_semana
    )
    if puntaje < MIN_SILHOUETTE:
        # Los grupos se tocan: esta instalación consume igual todos los días y
        # decir lo contrario sería dibujar una frontera donde no hay ninguna.
        return vacio.model_copy(
            update={"days_analyzed": len(formas), "silhouette": round(puntaje, 3)}
        )

    return DayArchetypesResult(
        device_id=device_id,
        period_start=start,
        period_end=stop,
        days_analyzed=len(formas),
        silhouette=round(puntaje, 3),
        archetypes=arquetipos,
        assignments=asignaciones,
    )
