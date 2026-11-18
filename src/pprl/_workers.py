"""Provide the pool of worker processes that hash and link use.

The workers ignore Ctrl+C and leave it to the main pprl process.
"""

import contextlib
import multiprocessing
import signal
from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


def _start_worker(attach: tuple | None) -> None:
    """Set up a new worker process.

    The worker first stops listening for Ctrl+C. Then, if ``attach`` is given
    as (function, *arguments), it calls that function. Link uses this to
    attach each worker to the hashes in shared memory.

    A terminal sends Ctrl+C to every process it started. If workers handled
    it, each would print its own traceback into the log. Instead, the main
    process reports Ctrl+C once, and the pool stops its workers.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if attach is not None:
        function, *arguments = attach
        function(*arguments)


class _WorkerProcess(multiprocessing.context.SpawnProcess):
    """A worker process that never receives Ctrl+C, on Linux and macOS.

    The process starts with Ctrl+C blocked, and keeps it blocked. Without
    this, a Ctrl+C that arrived while the worker was starting Python and
    importing pprl, numpy and pandas (before ``_start_worker`` runs) would
    stop the worker with a traceback. The main process blocks Ctrl+C only
    while it starts the worker, and reports any Ctrl+C after that.

    Windows has no way to block the signal. There, the worker ignores Ctrl+C
    as soon as it imports pprl, in the module that handles Ctrl+C at startup.
    """

    def start(self) -> None:
        if not hasattr(signal, "pthread_sigmask"):  # Windows
            return super().start()
        before = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        try:
            super().start()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, before)


class _WorkerContext(multiprocessing.context.SpawnContext):
    Process = _WorkerProcess


@contextlib.contextmanager
def worker_pool(
    workers: int, attach: tuple | None = None
) -> Generator[ProcessPoolExecutor]:
    """Open a pool of ``workers`` processes that ignore Ctrl+C.

    Each worker calls ``attach`` as it starts, if given (see
    ``_start_worker``).

    Workers are started with "spawn" on every platform, which is already the
    default on macOS and Windows. The "fork" method is unsafe with numpy and
    pandas. See ``_WorkerProcess`` for how workers avoid Ctrl+C.

    If the ``with`` block raises an exception (including Ctrl+C), the pool
    cancels the work that has not started yet. It waits only for the pieces
    that are already running.
    """
    pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=_WorkerContext(),
        initializer=_start_worker,
        initargs=(attach,),
    )
    try:
        yield pool
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
