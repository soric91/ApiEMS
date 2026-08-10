"""Caché de largo plazo para agregados de meses calendario ya cerrados.

Un mes finalizado es inmutable: no tiene sentido releer InfluxDB por él en
cada request. `cached_closed_month_total` lo guarda 7 días, con la identidad
de la empresa en la clave — mismo mecanismo que test_cache_isolation, de modo
que un cliente nunca recibe los agregados de otro.
"""

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import cast

from app.core.cache import clear_all_caches
from app.models.variables import Variable
from app.repositories.influx import InfluxRepository
from app.repositories.scoped import ScopedInfluxRepository
from app.services.energy.summary import period_summary
from app.services.influx.cache import cached_closed_month_total

POS = Variable.POWER_ACTIVE_TOTAL_POS
TZ = "America/Bogota"

# Junio 2026, ya cerrado (bordes alineados a medianoche local = 05:00 UTC).
JUN_START = datetime(2026, 6, 1, 5, 0, 0, tzinfo=UTC)
JUL_START = datetime(2026, 7, 1, 5, 0, 0, tzinfo=UTC)


class _Doble:
    """Devuelve un valor por empresa y cuenta cuántas veces se consultó."""

    def __init__(self, marca: float = 0.0) -> None:
        self.marca = marca
        self.consultas = 0

    async def energy_total(self, *args: object, **kwargs: object) -> Awaitable[float] | float:
        self.consultas += 1
        return self.marca


def _acotado(devices: frozenset[str], marca: float) -> tuple[ScopedInfluxRepository, _Doble]:
    doble = _Doble(marca)
    return ScopedInfluxRepository(cast(InfluxRepository, doble), devices), doble


class TestMesCerradoCachea:
    async def test_el_segundo_request_no_vuelve_a_consultar(self) -> None:
        clear_all_caches()
        repo, doble = _acotado(frozenset({"eq-a"}), 1.0)

        primero = await cached_closed_month_total(repo, POS, JUN_START, JUL_START, None)
        assert doble.consultas == 1

        segundo = await cached_closed_month_total(repo, POS, JUN_START, JUL_START, None)
        assert doble.consultas == 1  # sirvió de la caché, no de InfluxDB
        assert segundo == primero == 1.0

    async def test_el_dato_cacheado_es_inmutable(self) -> None:
        """Aunque la lectura real cambie, un mes cerrado devuelve lo ya
        calculado — el agregado no puede variar."""
        clear_all_caches()
        repo = _acotado(frozenset({"eq-a"}), 1.0)[0]

        cacheado = await cached_closed_month_total(repo, POS, JUN_START, JUL_START, None)
        doble = cast(_Doble, repo._inner)
        doble.marca = 999.0

        assert await cached_closed_month_total(repo, POS, JUN_START, JUL_START, None) == cacheado

    async def test_un_cliente_no_recibe_los_agregados_de_otro(self) -> None:
        clear_all_caches()
        repo_a, doble_a = _acotado(frozenset({"eq-a"}), 1.0)
        repo_b, doble_b = _acotado(frozenset({"eq-b"}), 2.0)

        de_a = await cached_closed_month_total(repo_a, POS, JUN_START, JUL_START, None)
        de_b = await cached_closed_month_total(repo_b, POS, JUN_START, JUL_START, None)

        assert de_a == 1.0
        assert de_b == 2.0
        assert doble_a.consultas == 1 and doble_b.consultas == 1


class TestAnualReusaMesesCerrados:
    async def test_el_anual_da_cache_frio_en_segundo_request(self) -> None:
        """`period_summary('year')` guarda los meses ya cerrados en la caché
        de largo plazo (7 días) y el mes abierto en la de 20s: un request
        inmediato posterior NO vuelve a tocar InfluxDB."""

        clear_all_caches()
        repo, doble = _acotado(frozenset({"eq-a"}), 3.0)

        await period_summary(repo, POS, "year", TZ, None)
        primera_vueltas = doble.consultas  # 12 buckets (11 cerrados + 1 abierto)

        # Segundos después, todo sale de las TTL caches: 0 lecturas.
        await period_summary(repo, POS, "year", TZ, None)
        assert doble.consultas == primera_vueltas
