"""Bounded sync-iterator → async-iterator bridge (shared by engines)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

_SENTINEL: Any = object()


async def iter_to_aiter[T](
    make_iter: Callable[[], Iterator[T]], *, maxsize: int = 256
) -> AsyncIterator[T]:
    """Run a sync iterator in a worker thread and stream its items to the loop.

    The queue is bounded: a slow async consumer parks the producer thread,
    which in turn parks the underlying network read — backpressure reaches the
    peer. Closing the async generator sets a stop flag and closes the sync
    generator, triggering its cleanup (e.g. association abort in ``find_iter``).
    Exceptions raised by the sync iterator re-raise in the async consumer.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
    stop = threading.Event()

    def _put(item: Any) -> bool:
        fut = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
        while True:
            if stop.is_set():
                fut.cancel()
                return False
            try:
                fut.result(timeout=0.25)
                return True
            except concurrent.futures.TimeoutError:
                continue
            except concurrent.futures.CancelledError:
                return False

    def _producer() -> None:
        gen: Iterator[T] | None = None
        try:
            gen = make_iter()
            for item in gen:
                if stop.is_set() or not _put(item):
                    return
        except BaseException as e:
            _put(e)
        finally:
            if gen is not None and hasattr(gen, "close"):
                gen.close()
            _put(_SENTINEL)

    task = asyncio.create_task(asyncio.to_thread(_producer))
    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        while not queue.empty():
            queue.get_nowait()  # unblock a parked producer put
        await task
