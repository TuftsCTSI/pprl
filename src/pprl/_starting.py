"""Handle Ctrl+C while pprl is starting up.

pprl's libraries take a moment to load, and pprl's main function can't report a
Ctrl+C until they have. This module is imported before anything else. Until
pprl's main function takes over, it catches Ctrl+C and stops pprl with the usual
message and exit code 130, instead of a traceback. The handler is installed
before pprl imports anything beyond what Python has already loaded, because
even the message catalog takes a moment to import.

A worker process ignores Ctrl+C and leaves it to the main process, starting
as early as it can. On Linux and macOS, the worker pool blocks
Ctrl+C before the workers even start.
"""

import os
import signal
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import FrameType


def stopped_while_starting(_signum: int, _frame: FrameType | None) -> None:
    """Stop pprl before it has finished starting, with exit code 130.

    Ctrl+C may arrive while the message catalog or the UI module is only
    partly imported. So the message is shown the normal way if possible, as
    plain text if not, and not at all during pprl's first moments.

    pprl exits immediately with ``os._exit``. That is safe because nothing is
    open or written yet. Raising SystemExit would not be safe: it could be
    raised in the middle of a library's imports, and some libraries (numpy,
    for one) catch it there, so pprl would carry on.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ignore a second Ctrl+C.
    try:
        from . import _messages as msg  # noqa: PLC0415 (may be half imported)

        message = msg.INTERRUPTED()
        try:
            from ._ui import fail  # noqa: PLC0415 (may be half imported)

            fail(message)
        except Exception:  # noqa: BLE001 (_ui may be half imported)
            first, *rest = message.splitlines()
            sys.stderr.write(
                f"[STOP] {first}\n" + "".join(f"       {r}\n" for r in rest)
            )
    except Exception:  # noqa: BLE001, S110 (pprl must stop, message or not)
        pass
    for stream in (sys.stdout, sys.stderr):
        stream.flush()
    os._exit(130)


# A worker process has already imported multiprocessing by the time it
# imports pprl. The main pprl process has not.
_multiprocessing = sys.modules.get("multiprocessing")
if _multiprocessing is not None and _multiprocessing.parent_process() is not None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
else:
    signal.signal(signal.SIGINT, stopped_while_starting)
