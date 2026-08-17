"""Comparación entre las sedes del MISMO cliente.

"¿Esta sede consume mucho?" no tiene respuesta absoluta: 400 kWh al día son
poco para una planta y muchísimo para una oficina. Lo que sí se puede
contestar es contra las otras sedes del mismo cliente, que es lo que este
servicio hace.

Deliberadamente NO cruza clientes. Comparar la sede de una empresa contra las
de otra exigiría datos que el token de este cliente no autoriza a ver, aunque
fuera solo para promediarlos, y el recorte por flota
(`ScopedInfluxRepository`) existe justamente para que eso no pueda pasar por
accidente. El precio es que el grupo de comparación es chico; la respuesta lo
dice en vez de disimularlo.

Solo se comparan sedes del mismo modo (con generación o de consumo puro): una
sede con solar importa estructuralmente menos, y meterla en el mismo ranking
haría ver a las demás como derrochadoras por no tener paneles.
"""

import asyncio
from datetime import timedelta

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import BenchmarkPeer, BenchmarkResult
from app.services.crm.fleet import FleetDevice
from app.services.influx.cache import cached_energy_total
from app.utils.period import start_of_day

# Cuántos días se promedian. Un mes absorbe la diferencia entre una semana
# ocupada y una tranquila.
DEFAULT_DAYS = 30
# Mínimo de sedes comparables —incluida la propia— para publicar una posición.
# Con dos, "estás por encima de la mediana" es decir "consumes más que la otra".
MIN_PEERS = 3
# Sin al menos dos sedes no hay contra qué comparar.
_MIN_PARA_COMPARAR = 2


def _percentil_de(propio: float, valores: list[float]) -> float:
    """Qué porcentaje de las sedes consume MENOS que esta.

    0 es la que menos consume del grupo, 100 la que más."""
    if len(valores) < _MIN_PARA_COMPARAR:
        return 0.0
    menores = sum(1 for v in valores if v < propio)
    return round(menores / (len(valores) - 1) * 100, 1)


def _mediana(valores: list[float]) -> float:
    ordenados = sorted(valores)
    medio = len(ordenados) // 2
    if len(ordenados) % 2 == 1:
        return ordenados[medio]
    return (ordenados[medio - 1] + ordenados[medio]) / 2


async def benchmark(
    repo: InfluxDataSource,
    devices: tuple[FleetDevice, ...],
    device_id: str,
    tz_name: str,
    days: int = DEFAULT_DAYS,
) -> BenchmarkResult:
    """Dónde queda esta sede frente a las otras del mismo cliente y modo."""
    propio = next((d for d in devices if d.id == device_id), None)
    stop = start_of_day(tz_name)
    start = stop - timedelta(days=days)

    vacio = BenchmarkResult(
        device_id=device_id,
        period_start=start,
        period_end=stop,
        days=days,
        own_kwh_per_day=None,
        median_kwh_per_day=None,
        percentile=None,
        peers=[],
        enough_peers=False,
    )
    if propio is None:
        return vacio

    # Mismo modo declarado: una sede con solar importa estructuralmente menos y
    # compararla contra las demás haría ver a estas como derrochadoras por no
    # tener paneles. `None` (nadie lo declaró) es su propio grupo: mezclarlo
    # con los declarados sería suponer lo que no se sabe.
    comparables = [d for d in devices if d.tiene_generacion == propio.tiene_generacion]

    totales = await asyncio.gather(
        *(
            cached_energy_total(repo, Variable.POWER_ACTIVE_TOTAL_POS, start, stop, d.id)
            for d in comparables
        )
    )
    pares = [
        BenchmarkPeer(
            device_id=d.id,
            name=d.nombre,
            site=d.sede,
            kwh_per_day=round(total / days, 2),
            is_self=d.id == device_id,
        )
        for d, total in zip(comparables, totales, strict=True)
        # Una sede sin ningún consumo en el mes no está midiendo: incluirla
        # bajaría la mediana del grupo con un cero que no es un ahorro.
        if total > 0
    ]
    propio_par = next((p for p in pares if p.is_self), None)
    if propio_par is None:
        return vacio

    valores = [p.kwh_per_day for p in pares]
    resultado = BenchmarkResult(
        device_id=device_id,
        period_start=start,
        period_end=stop,
        days=days,
        own_kwh_per_day=propio_par.kwh_per_day,
        median_kwh_per_day=round(_mediana(valores), 2),
        percentile=_percentil_de(propio_par.kwh_per_day, valores),
        # De menos a más consumo: se lee como un ranking de eficiencia.
        peers=sorted(pares, key=lambda p: p.kwh_per_day),
        enough_peers=len(pares) >= MIN_PEERS,
    )
    if not resultado.enough_peers:
        # Con dos sedes, "estás por encima de la mediana" solo dice "consumes
        # más que la otra". Se devuelve el dato propio sin la posición.
        return resultado.model_copy(update={"percentile": None, "median_kwh_per_day": None})
    return resultado
