import asyncio
from datetime import UTC, datetime, timedelta

from app.core.cache import cached, clear_all_caches


async def test_cached_avoids_second_call() -> None:
    calls = 0

    @cached(ttl_seconds=30)
    async def fn(x: int) -> int:
        nonlocal calls
        calls += 1
        return x * 2

    assert await fn(3) == 6
    assert await fn(3) == 6
    assert calls == 1


async def test_cached_distinguishes_different_args() -> None:
    calls: list[int] = []

    @cached(ttl_seconds=30)
    async def fn(x: int) -> int:
        calls.append(x)
        return x

    await fn(1)
    await fn(2)
    assert calls == [1, 2]


async def test_cached_buckets_datetime_within_ttl_window() -> None:
    calls = 0

    @cached(ttl_seconds=30)
    async def fn(t: datetime) -> int:
        nonlocal calls
        calls += 1
        return calls

    base = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    await fn(base)
    await fn(base + timedelta(seconds=5))  # misma ventana de 30s
    assert calls == 1


async def test_cached_different_ttl_bucket_misses() -> None:
    calls = 0

    @cached(ttl_seconds=10)
    async def fn(t: datetime) -> int:
        nonlocal calls
        calls += 1
        return calls

    base = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    await fn(base)
    await fn(base + timedelta(seconds=15))  # ventana distinta
    assert calls == 2


async def test_clear_all_caches_forces_recompute() -> None:
    calls = 0

    @cached(ttl_seconds=30)
    async def fn() -> int:
        nonlocal calls
        calls += 1
        return calls

    await fn()
    clear_all_caches()
    await fn()
    assert calls == 2


async def test_concurrent_calls_not_corrupted() -> None:
    """No hay locking explícito; solo verifica que no haya corrupción de datos."""

    @cached(ttl_seconds=30)
    async def fn(x: int) -> int:
        await asyncio.sleep(0)
        return x

    results = await asyncio.gather(*(fn(i) for i in range(5)))
    assert results == [0, 1, 2, 3, 4]
