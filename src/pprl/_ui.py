"""Show output in the terminal: the progress bar, tagged messages, and check results.

The wording comes from the message catalog. This module only lays it out.

Output Design
-------------
All user-facing status messages pass through this module. Every tag is
four characters in brackets, so the message text always starts at the same
column, called the text column:

    [ OK ] Everything is fine: a success summary       dark green
    [DONE] A command's bar, finished (terminal only)   bright green
    [NOTE] Something to know, shown to the user        bright blue
    [INFO] Technical details, written to the log only  dark blue
    [Y/N?] The user is asked to confirm                bright magenta
    [WARN] A possible issue; the command carries on    bright yellow
    [FAIL] One failed check, in a table (see below)    bright red
    [STOP] Fatal problem: the run stopped              bright red

When a command's checks find problems, they are shown in a table, using the
same tag column. The table ends with a [STOP] line saying that nothing was
written. Checks that pass show nothing:

    [FAIL] fields                 The config file has no `fields`.
                                  To fix: Write `fields` as ...
    [STOP] Nothing was written.

A message's second line (its fix) is indented to line up with its first
line, and so is every untagged line (such as inspect's block). Only a tag
ever appears left of the text column. In a terminal,
a line too long for the terminal is wrapped at its width (at most
WRAP_AT_MOST), and each wrapped line is indented the same way. The log, and
output that is not a terminal, keep one line per line of the message.

``with silenced():`` hides all output. Commands that run other commands as
steps of their own (sweep, check) use it.

The run log
-----------
Every line goes through ``_emit``. It appends the line, without color
codes, to the workspace's log (while ``run_log`` has the log open), and
then shows it. The terminal and the log are written from the same string,
so they can't drift apart. A line that can't be logged is not shown:
instead the run stops with LogFailedError. A finished bar's [DONE] line is
logged as it appears, but the bar's animation frames are not.

The progress bar
----------------
Each command has one bar, from its start to its end (see ``progress``). The
line never changes width, whatever the words and times:

    [ 42%] ██████████░░░░░░░░░░░░░░  Hashing records           00:00:36 / 00:01:30

The times are the time spent so far and the total time expected. The total
is kept steady (see ``_steadied``).

Output that is not a terminal shows no bar and no [DONE] line.
"""

import contextlib
import errno
import math
import os
import platform
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Self, TextIO

from colorama import Fore, Style

from . import _messages as msg
from . import _system, _wording
from ._io import for_appending
from ._shared import RELEASE, UserFacingError, is_disk_full, now

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Sequence
    from types import TracebackType

# ---------------------------------------------------------------------------
# Tag formatting
# ---------------------------------------------------------------------------

_TAG_WIDTH = 6  # Every tag: [XXXX]
TEXT_COLUMN = _TAG_WIDTH + 1  # Where the text of every line starts
_LABEL_WIDTH = 22  # Width of the label column in check tables
WRAP_AT_MOST = 100  # Wrap at the terminal's width, but never wider than this
# Each tag's (color, style).
_TAGS = {
    "OK": (Fore.GREEN, ""),
    "NOTE": (Fore.BLUE, Style.BRIGHT),
    "INFO": (Fore.BLUE, ""),
    "Y/N?": (Fore.MAGENTA, Style.BRIGHT),
    "WARN": (Fore.YELLOW, Style.BRIGHT),
    "FAIL": (Fore.RED, Style.BRIGHT),
    "STOP": (Fore.RED, Style.BRIGHT),
}
_DONE = "[DONE]"
_BRIGHT_GREEN = Style.BRIGHT + Fore.GREEN


@dataclass
class _Output:
    """How lines are shown and logged. This changes as a run goes on.

    ``_state`` holds all the state this module keeps.
    """

    silent: bool = False  # Whether to show nothing (see silenced)
    log: TextIO | None = None  # The open log, while a run is logged (see run_log)
    log_name: str = ""  # The open log's file name, for messages
    held: list | None = None  # Lines shown before a log exists (see held)
    bar: _Bar | None = None  # The innermost open bar, if any (see progress)
    on_screen: _Bar | None = None  # The bar drawn on screen, if any


_state = _Output()


# Matches a terminal color code. Color codes are left out of the log and
# don't count toward a line's width.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@contextlib.contextmanager
def silenced() -> Generator[None]:
    """Show nothing while the block runs."""
    before, _state.silent = _state.silent, True
    try:
        yield
    finally:
        _state.silent = before


# ---------------------------------------------------------------------------
# The run log
# ---------------------------------------------------------------------------


class LogFailedError(UserFacingError):
    """Raised when the log could not be opened or written.

    pprl's main function catches this before its crash handler, and shows the
    message without logging it.
    """


def _reason(error: OSError) -> str:
    """Return why a log file could not be used, as a plain phrase."""
    if isinstance(error, IsADirectoryError):
        kind = "folder"
    elif getattr(error, "winerror", None) in (32, 33):  # sharing or lock violation
        kind = "in use"
    elif isinstance(error, PermissionError):
        kind = "permission"
    elif is_disk_full(error):
        kind = "full"
    elif error.errno == errno.EROFS:
        kind = "read-only"
    elif error.errno in (errno.ENOENT, errno.EIO, errno.ENXIO, errno.ENODEV):
        kind = "unreachable"
    else:
        kind = "other"
    return msg.LOG_REASONS[kind]


def colored(text: str) -> str:
    """Return ``text`` as a terminal shows it, without colors if NO_COLOR is set."""
    return ANSI.sub("", text) if os.environ.get("NO_COLOR") else text


def _as_logged(text: str) -> str:
    """Return ``text`` as the log holds it.

    Color codes are removed, and user-supplied text is withheld.
    """
    return _wording.logged(ANSI.sub("", text))


def _log_line(line: str) -> str:
    """Append a line to the open log, if there is one, and flush it.

    Flushing means a run that is killed still leaves everything it showed.
    Before a log is open, the line is held instead, if lines are being held
    (see ``held``). Return the line as logged (see _as_logged).
    """
    line = _as_logged(line)
    if _state.log is None:
        if _state.held is not None:
            _state.held.append(line)
        return line
    try:
        _state.log.write(line + "\n")
        _state.log.flush()
    except OSError as error:
        _state.log = None  # Nothing more is written to the log.
        raise LogFailedError(
            msg.LOG_WRITE_FAILED(log=_state.log_name, reason=_reason(error))
        ) from None
    return line


def _emit(text: str, *, error: bool = False, hang: int = 0) -> None:
    """Log ``text``, then show it, on stderr if ``error`` is true.

    In a terminal, a line that is too long is wrapped, and each wrapped line
    is indented by ``hang``.
    """
    _log_line(text)
    stream = sys.stderr if error else sys.stdout
    shown = colored(_fitted(_wording.shown_text(text), hang, _wrap_width(stream)))
    bar = _state.on_screen
    if bar is None:
        print(shown, file=stream)
        return
    with bar.lock:  # Show the line on its own, above the bar.
        bar.clear()
        print(shown, file=stream)
        stream.flush()
        bar.draw()


def _is_terminal(stream: object) -> bool:
    """Tell whether ``stream`` is a terminal, not a file, a pipe, or a test stream."""
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _wrap_width(stream: object) -> int | None:
    """Find the width at which to wrap lines shown on ``stream``.

    Return None to leave lines unwrapped, when ``stream`` is not a terminal.
    """
    if not _is_terminal(stream):
        return None
    return min(shutil.get_terminal_size().columns, WRAP_AT_MOST)


def _cut(line: str, column: int) -> tuple[str, str]:
    """Split ``line`` after the first ``column`` visible characters.

    Color codes don't count. Return (the part before the split, the rest).
    """
    shown = i = 0
    while i < len(line) and shown < column:
        code = ANSI.match(line, i)
        if code:
            i = code.end()
        else:
            i, shown = i + 1, shown + 1
    while code := ANSI.match(line, i):  # such as a color reset after a tag
        i = code.end()
    return line[:i], line[i:]


def _wrapped(text: str, width: int) -> list:
    """Wrap ``text`` into lines of at most ``width`` visible characters.

    Color codes don't count, and lines break only at spaces. Spaces at the
    start of the text are kept. Spaces around a break are dropped.
    >>> _wrapped("one two three four", 9)
    ['one two', 'three', 'four']
    """
    lines, line, used = [], "", 0
    for chunk in re.findall(r"\s+|\S+", text):
        size = len(ANSI.sub("", chunk))
        if line.strip() and used + size > width:
            lines.append(line.rstrip())
            line, used = "", 0
        if lines and not line and chunk.isspace():
            continue
        line, used = line + chunk, used + size
    return [*lines, line.rstrip()]


def _fitted(text: str, hang: int, width: int | None) -> str:
    """Wrap each line of ``text`` that is longer than ``width``, for the terminal.

    The part of each line left of ``hang`` is kept as it is. Each wrapped
    line is indented by ``hang``.

    >>> print(_fitted("[WARN] one two three four five six", 7, 30))
    [WARN] one two three four five
           six
    """
    if width is None:
        return text
    lines = []
    for line in text.split("\n"):
        if len(ANSI.sub("", line)) <= width:
            lines.append(line)
            continue
        head, body = _cut(line, hang)
        wrapped = _wrapped(body, max(width - hang, 20))
        lines.append(head + wrapped[0])
        lines += [" " * hang + part for part in wrapped[1:]]
    return "\n".join(lines)


def _os_name() -> str:
    """Name the operating system, for the log's header.

    For example: "Windows 11 (10.0.26100)".
    """
    name = platform.system()
    if name == "Windows":
        return f"Windows {platform.release()} ({platform.version()})"
    if name == "Darwin":
        return f"macOS {platform.mac_ver()[0]} (Darwin {platform.release()})"
    if name == "Linux":
        with contextlib.suppress(OSError, KeyError):
            name = platform.freedesktop_os_release()["PRETTY_NAME"]
    return f"{name} ({platform.release()})"


@contextlib.contextmanager
def held() -> Generator[list[str]]:
    """Hold every line shown in the block, as logged, when no log is open.

    pprl destroy uses this for its receipt. Yield the list of lines.
    """
    before, _state.held = _state.held, []
    try:
        yield _state.held
    finally:
        _state.held = before


def ask(prompt: str, *, answers: Iterable[str] = ()) -> str | None:
    """Show ``prompt`` after the [Y/N?] tag and read the answer the user types.

    Return the answer, or None if there is no more input.

    The tag, prompt and answer are logged as one line, as the terminal shows
    them. But an answer that is not one of ``answers`` (ignoring case) is
    withheld from the log, because it is text the user supplied. If the
    answer is typed into a terminal, the terminal itself shows it. If it
    arrives some other way (such as piped in), it is shown here.
    """
    prompt = _tag("Y/N?") + prompt
    sys.stdout.write(colored(_wording.shown_text(prompt)))
    sys.stdout.flush()
    line = sys.stdin.readline()
    answer = line.rstrip("\r\n")
    if not _is_terminal(sys.stdin):
        sys.stdout.write(answer + "\n")
        sys.stdout.flush()
    known = answer.strip().lower() in {a.lower() for a in answers}
    _log_line(prompt + (answer if known else _wording.supplied(answer)))
    return answer.strip() if line else None


@dataclass
class Run:
    """A logged run. main sets its exit code, for the log's footer."""

    exit_code: int = 1


# How a run's record starts and ends in the log.
_RUN_START = msg.LOG_HEADER.text[: msg.LOG_HEADER.text.index("{")]
_RUN_END = msg.LOG_FOOTER.text[: msg.LOG_FOOTER.text.index("{")]


@dataclass
class LoggedRun:
    """A run as the log records it (see ``logged_runs``)."""

    header: str
    system: str | None = None
    lines: list = field(default_factory=list)
    footer: str | None = None
    after: list = field(default_factory=list)


def logged_runs(text: str) -> list[LoggedRun]:
    """List the runs recorded in ``text``, oldest first.

    ``text`` is a log, or a receipt, which contains the log. For each run,
    give its header, its system line, the lines it logged, its footer (None
    if the run was killed), and the lines between its footer and the next
    run's header (a blank line). Lines before the first header, which belong
    to a receipt itself, are left out.
    """
    runs = []
    for line in text.splitlines():
        if line.startswith(_RUN_START):
            runs.append(LoggedRun(line))
        elif not runs:
            continue
        elif runs[-1].system is None:
            runs[-1].system = line
        elif runs[-1].footer is not None:
            runs[-1].after.append(line)
        elif line.startswith(_RUN_END):
            runs[-1].footer = line
        else:
            runs[-1].lines.append(line)
    return runs


def _header(role: str, command: str) -> tuple[str, str]:
    """Write a record's first two lines: who ran what, when, and on which system."""
    return (
        msg.LOG_HEADER(
            time=_wording.when(now()),
            version=RELEASE,
            role=role,
            command=command,
        ),
        msg.LOG_SYSTEM(system=_os_name(), python=platform.python_version()),
    )


def info_log(text: str) -> None:
    """Log an [INFO] line for the coordinator.

    The line is never shown. It is not logged while output is silenced
    (during a step of another command).
    """
    if not _state.silent:
        _log_line(f"{msg.INFO_TAG} {text}")


def _safely[T](find: Callable[[], T]) -> T | None:
    """Return what ``find()`` finds about the computer, or None if it fails.

    An [INFO] line must never stop a run.
    """
    try:
        return find()
    except Exception:  # noqa: BLE001 (any failure at all, see above)
        return None


def _install() -> str:
    """Describe how pprl was installed, for the log."""
    method = _safely(_system.install_method)
    if not isinstance(method, dict):  # None if it could not be found
        return msg.INFO_UNKNOWN
    kind = msg.INSTALL_KINDS[method["kind"]](**method)
    source = msg.INSTALL_SOURCES[method["source"]](**{"wheel": "", "host": ""} | method)
    return f"{kind}, {source}"


def _safely_dict(find: Callable[[], object]) -> dict:
    """Return what ``find()`` finds as a dict, or {} if it fails (see _safely)."""
    found = _safely(find)
    return found if isinstance(found, dict) else {}


def _run_info() -> tuple[str, ...]:
    """Write the [INFO] lines that start every run.

    They say how pprl was installed, list its packages, and describe the
    computer.
    """
    packages = _wording.listed(
        f"{name} {found or msg.INFO_UNKNOWN}"
        for name, found in _safely_dict(_system.packages).items()
    )
    memory = _safely(_system.total_memory)
    return (
        msg.INFO_INSTALL(how=_install()),
        msg.INFO_PACKAGES(packages=packages),
        msg.INFO_COMPUTER(
            cpus=_system.cpus(),
            memory=msg.INFO_UNKNOWN if memory is None else _wording.size(memory),
        ),
    )


def _finished(started: float) -> str:
    """Write the [INFO] line that ends every run.

    It gives how long the run took and its peak memory.
    """
    peak = _safely(_system.peak_memory)
    return msg.INFO_FINISHED(
        seconds=time.perf_counter() - started,
        memory=msg.INFO_UNKNOWN if peak is None else _wording.size(peak),
    )


def record(role: str, command: str, lines: Iterable[str], exit_code: int) -> str:
    """Lay out a run's whole record as a log section, as text.

    pprl destroy uses this for its receipt, which outlives the log it
    deletes. As in the log, user-supplied text is withheld.
    """
    footer = msg.LOG_FOOTER(code=exit_code)
    return "".join(
        _as_logged(line) + "\n" for line in (*_header(role, command), *lines, footer)
    )


@contextlib.contextmanager
def run_log(path: Path, role: str, command: str) -> Generator[Run]:
    """Log the block's run in the log file ``path``.

    Write a header, every line the block shows, and a footer with the exit
    code. The footer is written even when the block raises.

    ``role`` says whose workspace it is, and ``command`` is the command as
    typed. Raise LogFailedError if the log cannot be opened or written.
    """
    try:
        handle = for_appending(path)
    except OSError as error:
        raise LogFailedError(
            msg.LOG_OPEN_FAILED(log=path.name, reason=_reason(error))
        ) from None
    run = Run()
    started = time.perf_counter()
    try:
        _state.log, _state.log_name = handle, path.name
        for line in _header(role, command):
            _log_line(line)
        for line in _run_info():
            info_log(line)
        yield run
    finally:
        try:
            if _state.log is not None:
                info_log(_finished(started))
            _log_line(msg.LOG_FOOTER(code=run.exit_code))
            _log_line("")
        finally:
            _state.log = None
            with contextlib.suppress(OSError):  # If it failed, it was reported.
                handle.close()


def show_warning(  # noqa: PLR0917 (warnings.showwarning's signature)
    message: Warning | str,  # noqa: ARG001 (warnings.showwarning's signature)
    category: type[Warning],
    filename: str,
    lineno: int,
    file: TextIO | None = None,  # noqa: ARG001 (as above)
    line: str | None = None,  # noqa: ARG001 (as above)
) -> None:
    """Show a Python warning as a [WARN] line, which also logs it.

    This replaces warnings.showwarning. It shows the warning's kind and
    place, never its text, which can quote data.
    """
    warn_user(
        msg.LIBRARY_WARNING(
            kind=category.__name__, place=f"{Path(filename).name}, line {lineno}"
        )
    )


def show(text: str = "", *, indent: int = TEXT_COLUMN, hang: int = 0) -> None:
    """Show untagged text, with every line starting at ``indent``.

    ``indent`` defaults to the text column. Use this for text such as a
    question or a row of a table. A wrapped line is indented ``hang`` more,
    as under the text of a numbered question.
    """
    if not _state.silent:
        lines = text.split("\n") if text else []
        _emit(
            "\n".join((" " * indent + line).rstrip() for line in lines),
            hang=indent + hang,
        )


def _tag(word: str) -> str:
    """Color a tag such as ``[WARN]``, and add the space after it."""
    color, style = _TAGS[word]
    return f"{style}{color}[{word:^4}]{Style.RESET_ALL} "


def _indented(text: str, indent: int) -> str:
    """Indent every line after the first by ``indent`` spaces."""
    return ("\n" + " " * indent).join(text.split("\n"))


def _tagged(word: str, message: str, *, error: bool = False) -> None:
    """Show ``message`` after its tag, with any further lines at the text column."""
    if not _state.silent:
        _emit(
            _tag(word) + _indented(message, TEXT_COLUMN),
            error=error,
            hang=TEXT_COLUMN,
        )


# ---------------------------------------------------------------------------
# User-facing messages
# ---------------------------------------------------------------------------


def ok_user(message: str) -> None:
    """Show a result with [ OK ]: what a command did, with its counts in bold."""
    _tagged("OK", getattr(message, "bold", message))


def note_user(message: str) -> None:
    """Show information that is neither a result nor a problem, with [NOTE]."""
    _tagged("NOTE", message)


# For each step, the amount of work (in the unit given in the comment) at
# which the step takes about half a minute or more on a laptop with 4 cores
# (measured with make bench). At this size, the user is told how to cancel it
# (see stoppable).
STOPPABLE_FROM = {
    "select": 80 * 1024**2,  # Bytes of the site's tables
    "clean": 500_000,  # Patients
    "hash": 200_000,  # Patients
    "pack": 4_000_000,  # Rows of the selected tables
    "link": 10_000_000_000,  # Pairs of records compared
    "merge": 2_500_000,  # Rows of every site's tables
}


def stoppable(step: str, size: int) -> bool:
    """Tell the user how to cancel ``step`` if it may take a while.

    The step may take a while if its ``size`` reaches STOPPABLE_FROM. Return
    whether the user was told.
    """
    if size < STOPPABLE_FROM[step]:
        return False
    note_user(msg.STOPPABLE())
    return True


def warn_user(message: str) -> None:
    """Show a caveat with [WARN]. The command carries on."""
    _tagged("WARN", message)


def error_user(message: str) -> None:
    """Show why the command stopped, with [STOP], on stderr."""
    if not _state.silent:
        _stop_bar()
    _tagged("STOP", message, error=True)


def fail(message: str) -> int:
    """Show an error message and return a failure code.

    Use it as: return fail(msg.SOMETHING(...))
    """
    error_user(message)
    return 1


# ---------------------------------------------------------------------------
# Info block (key-value metadata display)
# ---------------------------------------------------------------------------


def info_block(pairs: list[tuple[str, str]]) -> None:
    """Show (label, value) pairs at the text column, with the values lined up.

    A label that repeats the one above it is left out.
    """
    if _state.silent or not pairs:
        return
    label_width = max(len(label) for label, _ in pairs)
    hang = TEXT_COLUMN + label_width + 2
    labels = without_repeats([label for label, _ in pairs])
    for shown, (_, value) in zip(labels, pairs, strict=True):
        _emit(f"{' ' * TEXT_COLUMN}{shown:<{label_width}}  {value}".rstrip(), hang=hang)


def without_repeats(labels: list) -> list:
    """Return ``labels`` with "" in place of each label that repeats the one above.

    >>> without_repeats(["Member", "Member", "Site"])
    ['Member', '', 'Site']
    """
    return [
        "" if i and label == labels[i - 1] else label for i, label in enumerate(labels)
    ]


# ---------------------------------------------------------------------------
# Validation reporting
# ---------------------------------------------------------------------------


class ValidationContext:
    """Collect the problems a command's checks find, and show them as a table.

    Nothing is shown if there are no problems. Each row is (label, detail).
    """

    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []
        self._reported = 0

    def fail(self, label: str, detail: str) -> None:
        self.rows.append((label, detail))

    def attempt(
        self,
        label: str,
        problems: type[BaseException] | tuple[type[BaseException], ...],
    ) -> _Attempt:
        """Run a block, recording its problem as a row under ``label``.

        If the block raises one of ``problems``, the error's message becomes
        the row and the block stops there. The code after the block still
        runs, and ``failed`` says that the block failed. Any other exception
        propagates. A ``return`` inside the block returns only if nothing
        went wrong.

        >>> ctx = ValidationContext()
        >>> with ctx.attempt("Site", ValueError) as attempt:
        ...     raise ValueError("No site.")
        >>> attempt.failed, ctx.rows
        (True, [('Site', 'No site.')])
        """
        return _Attempt(self, label, problems)

    def fail_once(self, label: str, detail: str) -> int:
        """Record a failure under ``label``, conclude, and return the exit code.

        Use this for a problem that stops the command before its other checks.
        """
        self.fail(label, detail)
        self.conclude()
        return 1

    @property
    def has_failures(self) -> bool:
        return bool(self.rows)

    def first_failure(self) -> str | None:
        """Return the detail of the first problem, or None."""
        return self.rows[0][1] if self.rows else None

    def report(self) -> None:
        """Show the rows that have not been shown yet, on stderr."""
        rows = self.rows[self._reported :]
        self._reported = len(self.rows)
        if _state.silent or not rows:
            return
        _stop_bar()
        indent = TEXT_COLUMN + _LABEL_WIDTH + 1
        for label, detail in rows:
            prefix = _tag("FAIL")
            width = len(_wording.shown_text(label))
            if width > _LABEL_WIDTH:
                # A long label gets a line of its own.
                _emit(prefix + label, error=True)
                prefix, shown, width = " " * TEXT_COLUMN, "", 0
            else:
                shown = label
            padding = " " * (_LABEL_WIDTH - width)
            _emit(
                f"{prefix}{shown}{padding} {_indented(detail, indent)}",
                error=True,
                hang=indent,
            )

    def conclude(self) -> bool:
        """Print the table, ending with the summary if anything failed.

        Return whether anything failed.

        A command checks all of its inputs, then runs
        ``if ctx.conclude(): return 1`` before it writes anything.
        """
        self.report()
        if not self.has_failures:
            return False
        error_user(msg.NOTHING_WRITTEN())
        return True


class _Attempt:
    """A block of checks, run with ``with ctx.attempt(label, problems)``."""

    def __init__(
        self,
        ctx: ValidationContext,
        label: str,
        problems: type[BaseException] | tuple[type[BaseException], ...],
    ) -> None:
        self._ctx, self._label, self._problems = ctx, label, problems
        self.failed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if not isinstance(error, self._problems):
            return False
        self._ctx.fail(self._label, str(error))
        self.failed = True
        return True


# ---------------------------------------------------------------------------
# The progress bar
# ---------------------------------------------------------------------------

_BAR_WIDEST, _BAR_NARROWEST = 24, 4
_WORDS_WIDTH = 24  # Width of the step-words column; each step's words are padded
_TIMES_WIDTH = 19  # 00:00:36 / 00:01:30
# The width of everything on the line except the bar, including the gaps.
_AROUND_BAR = TEXT_COLUMN + 2 + _WORDS_WIDTH + 2 + _TIMES_WIDTH
_LONGEST_TIME = 100 * 3600 - 1
_FRAME_SECONDS = 0.2
_FIRST_ESTIMATE = 0.01  # The share done before the total time is shown
_CLEAR_LINE = "\r\x1b[K"


def _clock(seconds: float) -> str:
    """Write a time, rounded up to the second, in eight columns.

    Times are capped at 99 hours.

    >>> _clock(86), _clock(360000)
    ('00:01:26', '99:59:59')
    >>> _clock(0.003), _clock(1.2), _clock(3599.5)
    ('00:00:01', '00:00:02', '01:00:00')
    """
    hours, rest = divmod(min(math.ceil(seconds), _LONGEST_TIME), 3600)
    return f"{hours:02}:{rest // 60:02}:{rest % 60:02}"


# (limit, rounding): a time under the limit, in seconds, is rounded to a
# multiple of this many seconds.
_ROUNDING = ((60, 5), (10 * 60, 10), (60 * 60, 60))


def _rounded_to(seconds: float) -> int:
    """Choose the rounding for a total time of about ``seconds``.

    The longer the run, the less its estimate means to the second.
    """
    for under, rounded in _ROUNDING:
        if seconds < under:
            return rounded
    return 300


def _steadied(shown: int | None, estimate: float, spent: float) -> int:
    """Choose the total time to show, from the time already shown and a new estimate.

    ``shown`` is the total shown so far, ``estimate`` is the new estimate,
    and ``spent`` is the seconds spent. The result is rounded up. It stays
    unchanged until the estimate moves well past the rounding, so an
    estimate that wavers around a rounded value doesn't make the line
    flicker. It is never less than the time spent.

    >>> _steadied(None, 84, 30), _steadied(90, 81, 31), _steadied(90, 94, 32)
    (90, 90, 90)
    >>> _steadied(90, 96, 33), _steadied(90, 74, 34), _steadied(90, 91, 91)
    (100, 80, 100)
    >>> _steadied(None, 1234, 30), _steadied(None, 5000, 30)
    (1260, 5100)
    >>> _steadied(None, 2, 0.5), _steadied(5, 6, 1), _steadied(5, 8, 2)
    (5, 5, 10)
    """
    if shown is not None and spent <= shown:
        grain = _rounded_to(shown)
        if shown - 1.5 * grain < estimate <= shown + 0.5 * grain:
            return shown
    grain = _rounded_to(estimate)
    return math.ceil(estimate / grain) * grain


def _frame(
    share: float, words: str, spent: float, whole: int | None, columns: int
) -> str:
    """Draw the bar's line for a terminal ``columns`` wide.

    ``share`` of the command is done, after ``spent`` seconds of the
    ``whole`` time expected (if that is known yet). Filled cells are rounded
    down, so the bar is full only when the command is done. In a narrow
    terminal, only the bar shrinks. The last column stays free so that the
    line never wraps. If the terminal is narrower than the bar's minimum,
    the line is cut off.

    >>> ANSI.sub("", _frame(0.5, "Hashing records", 30, 60, 80))[:33]
    '[ 50%] ████████████░░░░░░░░░░░░  '
    >>> ANSI.sub("", _frame(0.99, "Hashing records", 30, 60, 80))[:33]
    '[ 99%] ███████████████████████░  '
    >>> ANSI.sub("", _frame(0.5, "Hashing records", 30, 60, 80))[-19:]
    '00:00:30 / 00:01:00'
    >>> ANSI.sub("", _frame(0.001, "Hashing records", 30, None, 80))[-8:]
    '--:--:--'
    """
    cells = min(_BAR_WIDEST, max(_BAR_NARROWEST, columns - 1 - _AROUND_BAR))
    filled = int(share * cells)
    expected = "--:--:--" if whole is None else _clock(whole)
    line = (
        f"{_BRIGHT_GREEN}[{int(share * 100):3}%] {'█' * filled}{Style.RESET_ALL}"
        f"{Style.DIM}{'░' * (cells - filled)}{Style.RESET_ALL}  "
        f"{words:<{_WORDS_WIDTH}}  "
        f"{Style.DIM}{_clock(spent)} / {expected}{Style.RESET_ALL}"
    )
    if columns - 1 < _AROUND_BAR + cells:
        line = f"{_cut(line, columns - 1)[0]}{Style.RESET_ALL}"
    return line


class _Bar:
    """A command's progress through its steps (word, share), and its drawing.

    The steps run in order. A bar opened inside another bar advances a step
    of the outer one (see ``progress``), and only the outermost bar is drawn.

    The main thread of the main process advances the bar, and a background
    thread draws it. This keeps the timer moving while the bar does not.
    It also means that anything that freezes writes to the console (such as
    Windows' QuickEdit) stops only the drawing. Each frame reaches the
    terminal in a single write, together with clearing the line. If the two
    were separate writes, the line would be blank between them whenever the
    main thread kept Python busy, and the timer would flash.
    """

    def __init__(
        self,
        command: str,
        steps: Sequence[tuple[str, float]],
        parent: _Bar | None = None,
    ) -> None:
        self.command, self.parent = command, parent
        self.started = self.step_started = time.perf_counter()
        whole = sum(share for _, share in steps)
        self.steps = [(word, share / whole) for word, share in steps]
        self.place, self.before, self.done, self.total = 0, 0.0, 0, 0
        self.spent = {}  # Seconds per step's words, which may repeat
        self.farthest = 0.0
        self.whole = None  # The total time shown (see _steadied)
        self.stopped = self.finished = False
        self.lock = threading.Lock()
        self.stream: TextIO = sys.stdout  # Where the bar is drawn (see show)
        self.thread: threading.Thread | None = None
        self.stop_drawing = threading.Event()

    def plan(self, steps: Sequence[tuple[str, float]]) -> None:
        """Replace the steps after this one, sharing out the rest of the bar.

        Use this for steps whose sizes depend on what the first step read.
        """
        share = self.steps[self.place][1]
        left = 1 - self.before - share
        whole = sum(s for _, s in steps) or 1
        self.steps[self.place + 1 :] = [(w, s / whole * left) for w, s in steps]

    def share(self) -> float:
        """Return how much of the command is done, never less than before.

        A total that turns out larger must not move the bar backward.
        """
        step = self.steps[self.place][1]
        part = min(self.done / self.total, 1.0) if self.total else 0.0
        self.farthest = max(self.farthest, min(self.before + step * part, 1.0))
        return self.farthest

    def _end_step(self, later: Sequence[tuple[str, float]]) -> None:
        """Record the time spent on this step.

        Log it too, unless the step's words come up again in ``later``.
        """
        word = self.steps[self.place][0]
        moment = time.perf_counter()
        self.spent[word] = self.spent.get(word, 0.0) + moment - self.step_started
        self.step_started = moment
        if all(w != word for w, _ in later):
            info_log(msg.INFO_STEP(step=word, seconds=self.spent[word]))

    def step(self, word: str, total: float | None) -> None:
        """Move on to the next step named ``word``, which has ``total`` units."""
        later = self.steps[self.place + 1 :]
        place = next((i for i, (w, _) in enumerate(later) if w == word), None)
        if place is None:  # A missing step must never stop a run.
            return
        self._end_step(later)
        self.before += sum(
            s for _, s in self.steps[self.place : self.place + 1 + place]
        )
        self.place += 1 + place
        self.done, self.total = 0, total
        self.spent.setdefault(word, 0.0)
        self._moved()

    def _moved(self) -> None:
        if self.parent is not None:
            self.parent.done, self.parent.total = self.share(), 1

    def advance(self, count: float) -> None:
        self.done += count
        self._moved()

    def add_work(self, count: float) -> None:
        self.total = (self.total or 0) + count

    def show(self) -> None:
        """Draw the bar, and keep redrawing it from a background thread."""
        self.stream = sys.stdout
        _state.on_screen = self
        with self.lock:
            self.draw()
        self.thread = threading.Thread(target=self._keep_drawing, daemon=True)
        self.thread.start()

    def _keep_drawing(self) -> None:
        while not self.stop_drawing.wait(_FRAME_SECONDS):
            with self.lock:
                self.draw()

    def draw(self) -> None:
        """Write one frame. Skip it if the output can't take it."""
        words = self.steps[self.place][0]
        spent = time.perf_counter() - self.started
        share = self.share()
        if share >= _FIRST_ESTIMATE:
            self.whole = _steadied(self.whole, spent / share, spent)
        columns = shutil.get_terminal_size().columns
        frame = _CLEAR_LINE + colored(_frame(share, words, spent, self.whole, columns))
        with contextlib.suppress(OSError, ValueError):
            self.stream.write(frame)
            self.stream.flush()

    def clear(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            self.stream.write(_CLEAR_LINE)
            self.stream.flush()

    def erase(self) -> None:
        """Stop drawing, and leave no trace of the bar on screen."""
        if _state.on_screen is not self:
            return
        self.stop_drawing.set()
        if self.thread is not None:
            self.thread.join()
        _state.on_screen = None
        self.clear()

    def finish(self) -> None:
        """Log the last step, then show [DONE] (in a terminal only).

        Call this when the command's work is done.
        """
        if self.finished or self.stopped:
            return
        self.finished = True
        self._end_step(())
        if self.parent is not None:
            self.parent.done, self.parent.total = 1, 1
            return
        was_shown = _state.on_screen is self
        self.erase()
        if was_shown:
            spent = _clock(time.perf_counter() - self.started)
            line = msg.PROGRESS_DONE(
                command=self.command, time=f"{Style.DIM}{spent}{Style.RESET_ALL}"
            )
            _emit(f"{_BRIGHT_GREEN}{_DONE}{Style.RESET_ALL} {line}")


def _draws(stream: object) -> bool:
    """Tell whether a bar can be drawn on ``stream``, a terminal that can redraw."""
    return _is_terminal(stream) and os.environ.get("TERM") != "dumb"


@contextlib.contextmanager
def progress(command: str, steps: Sequence[tuple[str, float]]) -> Generator[None]:
    """Show the command's bar while the block runs.

    The bar starts at the first of ``steps``, each given as (words, share of
    the bar). It ends with [DONE] when the block ends or when
    ``finish_progress`` is called, whichever comes first.

    Showing a problem (a [STOP] line, or a table of problems) erases the bar
    for good, and so does any exception. A line shown while the bar is up
    appears above it. When output is silenced or not a terminal, nothing is
    drawn, but each step's time is still logged.

    A bar opened inside another (as when sweep runs hash and link) advances
    the outer bar's current step, which should be started for it.
    """
    parent = _state.bar
    if parent is None and _state.silent:
        yield
        return
    bar = _Bar(command, steps, parent)
    _state.bar = bar
    try:
        if parent is None and _draws(sys.stdout):
            bar.show()
        yield
        bar.finish()
    finally:
        _state.bar = parent
        if parent is None:
            bar.erase()


def step(words: str, total: int = 0) -> None:
    """Move on to the next step of the bar, the one named ``words``.

    ``total`` is how many units ``advance`` will count, in whatever unit fits.
    """
    if _state.bar is not None:
        _state.bar.step(words, total)


def advance(count: float = 1) -> None:
    """Mark ``count`` units of the current step as done."""
    if _state.bar is not None:
        _state.bar.advance(count)


def add_work(count: float) -> None:
    """Add ``count`` units to the current step's total."""
    if _state.bar is not None:
        _state.bar.add_work(count)


def plan_steps(steps: Sequence[tuple[str, float]]) -> None:
    """Replace the steps after the current one (see _Bar.plan)."""
    if _state.bar is not None:
        _state.bar.plan(steps)


def finish_progress() -> None:
    """End the command's bar with [DONE].

    Call this before the lines that report what the command did. A bar
    inside another bar simply ends.
    """
    if _state.bar is not None:
        _state.bar.finish()


def _stop_bar() -> None:
    """Erase the bar for good, because a problem is about to be shown."""
    bar = _state.bar
    while bar is not None:
        bar.stopped = True
        bar.erase()
        bar = bar.parent
