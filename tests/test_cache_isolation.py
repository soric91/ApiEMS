"""La clave del caché tiene que decir de quién son los datos.

`@cached` armaba su clave con `str()` de cada argumento, y el primero es el
repositorio acotado. Un objeto sin `__str__` propio se convierte en
`<ScopedInfluxRepository object at 0x7f3a…>`: **su dirección de memoria**.

Como `get_influx_repository` crea uno nuevo en cada petición, el recolector
reutiliza direcciones constantemente. Cuando la dirección liberada por la
empresa A la recibe el repositorio de la empresa B, la clave colisiona y B se
lleva el consumo cacheado de A.

No es teórico: `tests/conftest.py` tenía un `clear_all_caches()` puesto
justamente para que este efecto no ensuciara los tests. Se vio, se tapó donde
molestaba, y en producción no hay nada equivalente.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from app.core.cache import cached, clear_all_caches
from app.repositories.influx import InfluxRepository
from app.repositories.scoped import ScopedInfluxRepository
from app.services.influx.cache import cached_field_keys

EMPRESA_A = frozenset({"eq-de-la-empresa-a"})
EMPRESA_B = frozenset({"eq-de-la-empresa-b"})

DESDE = datetime(2026, 8, 1, tzinfo=UTC)
HASTA = datetime(2026, 8, 2, tzinfo=UTC)


class _Interior:
    """Devuelve un valor propio de la empresa.

    Distinto por empresa a propósito: si las dos devolvieran lo mismo, el test
    pasaría con o sin caché y no probaría nada. Aquí, que B reciba el valor de
    A es exactamente el síntoma de la fuga.
    """

    def __init__(self, marca: float = 0.0) -> None:
        self.marca = marca
        self.llamadas = 0
        self.llamadas_field_keys = 0

    async def energy_total(self, *args: object, **kwargs: object) -> float:
        self.llamadas += 1
        return self.marca

    async def field_keys(self, *args: object, **kwargs: object) -> list[str]:
        self.llamadas_field_keys += 1
        return [f"campo-de-{self.marca}"]


_MARCAS = {EMPRESA_A: 1.0, EMPRESA_B: 2.0}


def _acotado(devices: frozenset[str]) -> ScopedInfluxRepository:
    return _con_doble(devices)[0]


def _con_doble(devices: frozenset[str]) -> tuple[ScopedInfluxRepository, _Interior]:
    """El repositorio y su doble, para poder contar llamadas sin hurgar adentro."""
    interior = _Interior(_MARCAS.get(devices, 9.0))
    return ScopedInfluxRepository(cast(InfluxRepository, interior), devices), interior


class _Contador:
    """Cuántas veces se ejecutó el cuerpo de la función cacheada.

    Compartido y no por repositorio: cada petición trae su propio interior, así
    que contar adentro no diría si dos peticiones distintas compartieron caché,
    que es justo lo que se quiere saber.
    """

    veces = 0


_contador = _Contador()


@cached(ttl_seconds=300)
async def _consulta_cacheada(repo: Any, desde: datetime, hasta: datetime) -> float:
    _contador.veces += 1
    return await repo._inner.energy_total(desde, hasta)


class TestDosEmpresasNoCompartenCache:
    async def test_la_respuesta_de_una_no_le_llega_a_la_otra(self) -> None:
        """El test que da nombre al archivo.

        Si la clave se armara con la dirección de memoria, este test seguiría
        pasando *casi siempre* —dos objetos vivos a la vez tienen direcciones
        distintas— y fallaría en producción cuando el recolector reutilice una.
        Por eso el que muerde de verdad es el siguiente.
        """
        a = await _consulta_cacheada(_acotado(EMPRESA_A), DESDE, HASTA)
        b = await _consulta_cacheada(_acotado(EMPRESA_B), DESDE, HASTA)

        assert a != b

    async def test_una_direccion_reutilizada_no_confunde_a_las_empresas(
        self,
    ) -> None:
        """Reproduce el caso real: el objeto de A se libera y B cae en su lugar.

        Se fuerza forzando la liberación entre las dos llamadas. Con la clave
        por dirección, la segunda empresa recibe el valor de la primera.
        """
        repo_a = _acotado(EMPRESA_A)
        direccion_a = id(repo_a)
        valor_a = await _consulta_cacheada(repo_a, DESDE, HASTA)

        del repo_a  # libera la dirección

        repo_b = _acotado(EMPRESA_B)
        valor_b = await _consulta_cacheada(repo_b, DESDE, HASTA)

        # Si el recolector no reutilizó la dirección, el test no probó nada:
        # se deja anotado en vez de dar un falso verde silencioso.
        reutilizada = id(repo_b) == direccion_a
        assert valor_a != valor_b, (
            f"La empresa B recibió el valor cacheado de A (dirección reutilizada: {reutilizada})"
        )

    async def test_la_misma_empresa_si_reusa_el_cache(self) -> None:
        """El test que muerde, y el que delata la clave por dirección.

        Dos peticiones de la misma empresa traen objetos distintos —uno por
        petición— con direcciones distintas. Si la clave fuera la dirección, la
        segunda **nunca** encontraría lo cacheado por la primera: el caché no
        acertaría jamás, y además cada entrada quedaría ocupando lugar con una
        clave que no se va a volver a usar.

        Se cuenta cuántas veces se ejecutó el cuerpo, no el valor devuelto: el
        doble devuelve siempre lo mismo, así que comparar valores pasaría con
        caché y sin él.
        """
        clear_all_caches()
        _contador.veces = 0

        await _consulta_cacheada(_acotado(EMPRESA_A), DESDE, HASTA)
        await _consulta_cacheada(_acotado(EMPRESA_A), DESDE, HASTA)

        assert _contador.veces == 1


class TestLaIdentidadDescribeLaFlota:
    def test_dos_flotas_distintas_dan_identidades_distintas(self) -> None:
        assert _acotado(EMPRESA_A).cache_identity != _acotado(EMPRESA_B).cache_identity

    def test_la_misma_flota_da_la_misma_identidad(self) -> None:
        """Aunque sean objetos distintos: es lo que permite que el caché sirva
        entre peticiones del mismo cliente."""
        assert _acotado(EMPRESA_A).cache_identity == _acotado(EMPRESA_A).cache_identity

    def test_el_orden_de_los_equipos_no_cambia_la_identidad(self) -> None:
        """La flota llega como conjunto; si el orden alterara la clave, el mismo
        cliente tendría entradas distintas según el capricho del `frozenset`."""
        uno = ScopedInfluxRepository(cast(InfluxRepository, _Interior()), frozenset({"a", "b"}))
        otro = ScopedInfluxRepository(cast(InfluxRepository, _Interior()), frozenset({"b", "a"}))

        assert uno.cache_identity == otro.cache_identity

    def test_no_es_la_direccion_de_memoria(self) -> None:
        """Lo que fallaba. Dos objetos con la misma flota tienen direcciones
        distintas, y aun así tienen que compartir caché."""
        uno = _acotado(EMPRESA_A)
        otro = _acotado(EMPRESA_A)

        assert id(uno) != id(otro)
        assert uno.cache_identity == otro.cache_identity
        assert hex(id(uno)) not in uno.cache_identity


class TestElTiempoSigueEntrandoEnLaClave:
    async def test_rangos_distintos_no_comparten_entrada(self) -> None:
        """La identidad de la empresa se suma a lo que ya había; no lo
        reemplaza."""
        repo = _acotado(EMPRESA_A)

        # El doble devuelve siempre lo mismo, así que un valor igual no prueba
        # nada. Lo que se mira es que el interior se haya llamado dos veces.
        clear_all_caches()
        _contador.veces = 0

        await _consulta_cacheada(repo, DESDE, HASTA)
        await _consulta_cacheada(repo, DESDE, HASTA + timedelta(days=5))

        assert _contador.veces == 2


class TestElCacheDeFieldKeys:
    """La consulta más cara del panel, y la que más obvio era cachear.

    `schema.fieldKeys` con predicado barre datos pese al nombre. Su respuesta
    —qué variables tienen datos— cambia cuando alguien da de alta un medidor en
    el CRM, no entre una carga del panel y la siguiente.
    """

    async def test_la_segunda_carga_no_vuelve_a_consultar(self) -> None:
        clear_all_caches()
        repo, interior = _con_doble(EMPRESA_A)

        await cached_field_keys(repo, timedelta(days=7))
        await cached_field_keys(repo, timedelta(days=7))

        assert interior.llamadas_field_keys == 1

    async def test_otra_empresa_no_recibe_las_variables_de_la_primera(
        self,
    ) -> None:
        """El mismo defecto de la Fase 0, aplicado a algo peor: acá lo que se
        filtraría es qué mide otra empresa."""
        clear_all_caches()

        de_a = await cached_field_keys(_acotado(EMPRESA_A), timedelta(days=7))
        de_b = await cached_field_keys(_acotado(EMPRESA_B), timedelta(days=7))

        assert de_a == ["campo-de-1.0"]
        assert de_b == ["campo-de-2.0"]

    async def test_una_ventana_distinta_es_otra_pregunta(self) -> None:
        clear_all_caches()
        repo, interior = _con_doble(EMPRESA_A)

        await cached_field_keys(repo, timedelta(days=7))
        await cached_field_keys(repo, timedelta(days=30))

        assert interior.llamadas_field_keys == 2
