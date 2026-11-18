"""Set up and guard every run of pprl.

Every run in a workspace starts with ``in_workspace``. It finds the
workspace marker, opens the log, and checks the marker and the bundle. Then
it runs the command inside ``guarded``, which reports Ctrl+C and crashes.
"""

import contextlib
import signal
import sys
import warnings
from typing import TYPE_CHECKING, Never

from colorama import just_fix_windows_console

from . import _messages as msg
from . import _system
from ._commands import workspace_bundle
from ._io import workspace
from ._shared import RELEASE, error_place, is_disk_full
from ._ui import LogFailedError, fail, info_log, run_log, show_warning
from ._workspace import WorkspaceError
from ._workspace import read as read_workspace
from ._workspace import verify as verify_workspace

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import FrameType

    from ._workspace import Marker


def _unexpected(error: BaseException) -> str:
    """Return the message for a crash: the kind of error and where it happened.

    The message leaves out the exception's text, because that text can quote
    data (such as a value from a patient file).
    """
    return msg.UNEXPECTED(
        kind=type(error).__name__, place=error_place(error), version=RELEASE
    )


def stopped(_signum: int, _frame: FrameType | None) -> Never:
    """Raise KeyboardInterrupt on Ctrl+C, so that ``guarded`` can report it.

    Any further Ctrl+C is ignored while the first is reported and the run
    cleans up. Otherwise it could interrupt the report with a traceback.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt


def prepare_terminal() -> None:
    """Set up the terminal for a run.

    Turn on colors on Windows, and use UTF-8 output. Also make Ctrl+C raise
    KeyboardInterrupt again, for ``guarded`` to report. Until this point,
    the startup handler stopped pprl on Ctrl+C.
    """
    signal.signal(signal.SIGINT, stopped)
    just_fix_windows_console()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8")


def _crash(error: BaseException) -> str:
    """Write the [INFO] line for a crash or for Ctrl+C.

    The line lists every place in pprl's code the error passed through, and
    the place in the library where it was raised. It then lists the kinds of
    any errors that were being handled when it was raised. It never includes
    an error's text or a variable's value.
    """
    chain = " → ".join(
        msg.INFO_CRASH_FRAME(file=file, line=line, function=function)
        for file, line, function in _system.crash_frames(error)
    )
    causes = "".join(msg.INFO_CRASH_CAUSE(kind=k) for k in _system.causes(error))
    return msg.INFO_CRASH(chain=f"{type(error).__name__} at {chain}{causes}")


def guarded(body: Callable[[], int]) -> int:
    """Run ``body()`` and return its exit code.

    Return 130 if the user stopped it with Ctrl+C. Return 1 after any
    unexpected error. The error is reported by its kind and place, and the
    log records where it happened (see ``_crash``).
    """
    try:
        return body()
    except KeyboardInterrupt as e:
        info_log(_crash(e))
        fail(msg.INTERRUPTED())
        return 130
    except LogFailedError:  # in_workspace reports this without logging it
        raise
    except OSError as e:
        info_log(_crash(e))
        return fail(msg.DISK_FULL() if is_disk_full(e) else _unexpected(e))
    except Exception as e:  # noqa: BLE001 (report anything at all, without its text)
        info_log(_crash(e))
        return fail(_unexpected(e))


def in_workspace(command_line: str, body: Callable[[Marker, dict | None], int]) -> int:
    """Run ``body(marker, bundle)`` in the workspace and return its exit code.

    The workspace is the current folder.

    Every run in a workspace starts the same way: find the marker, open the
    log, check the marker and the bundle, and then run the body. A problem
    found before the log is open can only be shown on screen. Everything
    after that is also logged, under a header that names ``command_line``.
    """
    folder = workspace()
    try:
        marker = read_workspace(folder)
    except WorkspaceError as e:
        return fail(str(e))
    role = msg.LOG_ROLES[marker.role](site=marker.site)
    try:
        with (
            run_log(folder / marker.log_name, role, command_line) as run,
            warnings.catch_warnings(),
        ):
            # This is the documented hook, though typeshed declares it a
            # plain function.
            warnings.showwarning = show_warning  # ty: ignore[invalid-assignment]
            run.exit_code = guarded(lambda: _verified(marker, folder, body))
    except LogFailedError as e:  # shown on screen, but not logged
        return fail(str(e))
    return run.exit_code


def _verified(
    marker: Marker, folder: Path, body: Callable[[Marker, dict | None], int]
) -> int:
    """Check the marker and the bundle, then run ``body(marker, bundle)``.

    ``bundle`` is the workspace's bundle, or None in the coordinator's
    workspace, which has none.
    """
    try:
        verify_workspace(marker)
        bundle = workspace_bundle(marker, folder)
    except WorkspaceError as e:
        return fail(str(e))
    return body(marker, bundle)
