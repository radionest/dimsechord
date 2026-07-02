import asyncio
import threading
import time

import pytest

from dimsechord._bridge import iter_to_aiter


@pytest.mark.asyncio
async def test_items_stream_in_order() -> None:
    out = [x async for x in iter_to_aiter(lambda: iter(range(10)))]
    assert out == list(range(10))


@pytest.mark.asyncio
async def test_exception_propagates() -> None:
    def boom():
        yield 1
        raise RuntimeError("boom")

    it = iter_to_aiter(boom)
    assert await anext(it) == 1
    with pytest.raises(RuntimeError, match="boom"):
        await anext(it)


@pytest.mark.asyncio
async def test_bounded_queue_backpressure() -> None:
    produced: list[int] = []

    def gen():
        for i in range(50):
            produced.append(i)
            yield i

    it = iter_to_aiter(gen, maxsize=4)
    assert await anext(it) == 0
    await asyncio.sleep(0.5)  # producer runs ahead only as far as the queue allows
    assert len(produced) <= 7  # maxsize + consumed + one in-flight put
    assert len([x async for x in it]) == 49


@pytest.mark.asyncio
async def test_early_close_closes_sync_generator() -> None:
    closed = threading.Event()

    def gen():
        try:
            for i in range(1000):
                yield i
                time.sleep(0.01)
        finally:
            closed.set()

    it = iter_to_aiter(gen, maxsize=2)
    assert await anext(it) == 0
    await it.aclose()
    assert closed.wait(timeout=5)
