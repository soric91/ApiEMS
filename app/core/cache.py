"""Cache en memoria (TTL) para respuestas costosas de InfluxDB.

Nunca se aplica al tiempo real (RealtimeState / WebSocket) — solo a
consultas históricas repetidas por múltiples clientes (dashboard, KPIs,
analytics, reportes). Los `datetime` en los argumentos se redondean a la
ventana del TTL: si no, un `stop=now()` con microsegundos distintos en
cada request generaría una clave distinta y el cache nunca acertaría.
"""

import functools
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, ParamSpec, TypeVar

from cachetools import TTLCache

P = ParamSpec("P")
R = TypeVar("R")

# Registro de todas las TTLCache creadas por @cached, para poder vaciarlas
# entre tests (evita que dos objetos con distinta identidad lógica pero el
# mismo id() reutilizado por el GC colisionen en la clave de cache).
_registry: list[TTLCache[Any, Any, float]] = []


def clear_all_caches() -> None:
    for cache in _registry:
        cache.clear()


def _normalize(value: object, ttl_seconds: int) -> object:
    # Un objeto que sabe identificarse manda su identidad, no su dirección.
    # Sin esto la clave sería `<... object at 0x7f3a...>`, que describe dónde
    # está el objeto y no de quién son los datos: dos empresas distintas pueden
    # caer en la misma dirección y compartir entrada de caché.
    identity = getattr(value, "cache_identity", None)
    if isinstance(identity, str):
        return identity
    if isinstance(value, datetime):
        return int(value.timestamp() // ttl_seconds)
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Enum):
        return value.value
    return value


def cached(ttl_seconds: int) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Cachea el resultado de una función async por `ttl_seconds`.

    Una instancia de `TTLCache` por función decorada (closure), compartida
    entre todas las llamadas mientras el proceso viva.
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        cache: TTLCache[str, R, float] = TTLCache[str, R, float](maxsize=256, ttl=ttl_seconds)
        _registry.append(cache)

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            key_parts = [str(_normalize(a, ttl_seconds)) for a in args]
            key_parts += [f"{k}={_normalize(v, ttl_seconds)}" for k, v in sorted(kwargs.items())]
            key = "|".join(key_parts)
            if key in cache:
                return cache[key]
            result = await fn(*args, **kwargs)
            cache[key] = result
            return result

        return wrapper

    return decorator
