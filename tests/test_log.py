"""Test the run log.

Every run in a workspace is logged in the workspace's log (such as
site_a.log): a header, everything the run showed, and a footer with the
exit code. A log that can't be opened or written stops the run. The
usability tests check, for every case, that the log matches what the
terminal showed. A maintainability test checks that nothing in pprl prints
around the log.
"""

import errno
import io
import os
import platform
import re
import signal
import struct
import sys
import warnings
import zlib
from importlib.metadata import version
from typing import TYPE_CHECKING, Never, override

import pandas as pd
import pytest

from pprl import _hash, _pprl_file, _runs, _system, _ui, _wording
from pprl import _messages as msg
from pprl._io import for_appending, read_text, write_text
from pprl._ui import ANSI
from tests.commands import run_text
from tests.helpers import (
    PROJECT_SETTINGS,
    files_in,
    log_info_lines,
    log_of,
    log_sections,
    make_workspace,
    run_in,
    shows,
    strip_ansi,
    synthesize_patients,
    write_bundle,
    write_yaml,
)
from tests.test_usability import style_problems

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from pprl._workspace import Marker

pytestmark = pytest.mark.reliability

_HEADER = re.compile(
    r"===== \d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4} · pprl (?P<version>\S+) · "
    r"(?P<role>.+) · (?P<command>.+) ====="
)


def _hash_workspace(folder: Path) -> None:
    """Make a site's workspace that holds the cleaned patient file hash reads."""
    make_workspace(folder)
    assert synthesize_patients(folder, n=5) == 0


def _inspect_workspace(folder: Path, role: str = "site") -> Marker:
    """Make a workspace that holds a file to inspect."""
    marker = make_workspace(folder, role=role)
    write_text(folder / "hashes.csv", "study_id,site,hash\n")
    return marker


class _FailingLog:
    """A log file whose writes start failing with ``error`` after ``writes`` writes."""

    def __init__(self, handle: TextIO, writes: int, error: OSError) -> None:
        self.handle, self.writes, self.error = handle, writes, error

    def write(self, text: str) -> int:
        if self.writes == 0:
            raise self.error
        self.writes -= 1
        return self.handle.write(text)

    def flush(self) -> None:
        self.handle.flush()

    def close(self) -> None:
        self.handle.close()


class TestSections:
    """The section each run adds to the log."""

    def test_one_section_per_run(self, tmp_path: Path) -> None:
        """Each run appends one section to the log.

        The section has a header naming the time, the release, the role and
        the command; the system; what the run showed; and the exit code.
        """
        _inspect_workspace(tmp_path)
        outputs = [run_in(tmp_path, ["inspect", "hashes.csv"]) for _ in range(2)]
        outputs.append(run_in(tmp_path, ["inspect", "missing.csv"]))
        sections = log_sections(tmp_path)
        assert len(sections) == 3, sections
        for (rc, output), (header, system, body, footer) in zip(
            outputs, sections, strict=True
        ):
            match = _HEADER.fullmatch(header)
            assert match, header
            assert match["version"] == version("pprl")
            assert match["role"] == "site_a"
            assert match["command"].startswith("pprl inspect ")
            assert system.endswith(f" · Python {platform.python_version()}")
            assert body == output
            assert footer == f"----- exit code {rc} -----"
        assert [rc for rc, _ in outputs] == [0, 0, 1]

    def test_roles_named(self, tmp_path: Path) -> None:
        """The header names whose workspace it is, for every role."""
        expected = {
            "coordinator": "coordinator",
            "site": "site_a",
            "linkage": "linkage agent",
            "merge": "merge agent",
        }
        for role, shown in expected.items():
            folder = tmp_path / role
            _inspect_workspace(folder, role=role)
            run_in(folder, ["inspect", "hashes.csv"])
            [(header, _, _, _)] = log_sections(folder)
            match = _HEADER.fullmatch(header)
            assert match, header
            assert match["role"] == shown, header

    def test_text_file(self, tmp_path: Path) -> None:
        """The log is UTF-8 text with the platform's line endings."""
        _inspect_workspace(tmp_path)
        run_in(tmp_path, ["inspect", "hashes.csv"])
        raw = log_of(tmp_path).read_bytes()
        assert "·".encode() in raw
        assert (b"\r\n" in raw) == (os.linesep == "\r\n"), raw[:200]
        assert b"\x1b" not in raw, "No color codes"

    def test_no_identifiers(self, tmp_path: Path) -> None:
        """The log never holds the marker's ids."""
        marker = _inspect_workspace(tmp_path)
        run_in(tmp_path, ["inspect", "hashes.csv"])
        log = read_text(log_of(tmp_path))
        for identifier in (marker.workspace, marker.project):
            assert identifier not in log, identifier


class TestStrictLogging:
    """A run that cannot log does not run."""

    def test_log_cannot_open(self, tmp_path: Path) -> None:
        """A log that can't be opened stops the run before anything is done.

        A message is shown on the terminal.
        """
        _hash_workspace(tmp_path)
        log_of(tmp_path).mkdir()  # A folder can't be opened as a file.
        before = files_in(tmp_path)
        rc, output = run_in(tmp_path, ["hash"])
        assert rc == 1, output
        assert shows(msg.LOG_OPEN_FAILED, output), output
        assert not style_problems(output, rejected=True), output
        assert files_in(tmp_path) == before, "Nothing is written"

    def test_write_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failed write to the log stops the run.

        Nothing more is shown or written. The message says why, on the
        terminal only.
        """
        _hash_workspace(tmp_path)
        full = OSError(errno.ENOSPC, "No space left on device")
        monkeypatch.setattr(
            _ui, "for_appending", lambda path: _FailingLog(for_appending(path), 2, full)
        )
        rc, output = run_in(tmp_path, ["hash"])
        assert rc == 1, output
        assert output.startswith("[STOP]"), "The first line to log was not shown"
        assert shows(
            msg.LOG_WRITE_FAILED(log="site_a.log", reason=msg.LOG_REASONS["full"]),
            output,
        ), output
        assert not style_problems(output, rejected=True), output
        assert not (tmp_path / "outbox" / "linkage" / "site_a.hashes.pprl").exists(), (
            "Nothing is written"
        )
        [(_, _, body, footer)] = log_sections(tmp_path)
        assert body == "", "Nothing after the header is logged"
        assert footer is None, "Nothing after the header is logged"

    def test_reasons(self) -> None:
        """The reason a log couldn't be used is a plain phrase, never the error's text.

        The phrase is chosen by the kind of error.
        """

        class _InUseError(PermissionError):
            winerror = 32  # Windows' code for a file in use, tested on any platform

        in_use = _InUseError(errno.EACCES, "SHOULD_NOT_APPEAR")
        for error, kind in (
            (in_use, "in use"),
            (PermissionError(errno.EACCES, "SHOULD_NOT_APPEAR"), "permission"),
            (IsADirectoryError(errno.EISDIR, "SHOULD_NOT_APPEAR"), "folder"),
            (OSError(errno.ENOSPC, "SHOULD_NOT_APPEAR"), "full"),
            (OSError(errno.EROFS, "SHOULD_NOT_APPEAR"), "read-only"),
            (OSError(errno.EIO, "SHOULD_NOT_APPEAR"), "unreachable"),
            (OSError(errno.EINVAL, "SHOULD_NOT_APPEAR"), "other"),
        ):
            assert _ui._reason(error) == msg.LOG_REASONS[kind], kind


class TestInterruptions:
    """How the log records Ctrl+C and warnings."""

    def test_ctrl_c(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ctrl+C stops the run with its own message and exit code 130.

        Both are logged.
        """
        _hash_workspace(tmp_path)

        def interrupted(_func: object, _configuration: object) -> Never:
            raise KeyboardInterrupt

        monkeypatch.setattr("pprl._commands.run_command", interrupted)
        rc, output = run_in(tmp_path, ["hash"])
        assert rc == 130, output
        assert shows(msg.INTERRUPTED, output), output
        assert not style_problems(output, rejected=True), output
        [(_, _, body, footer)] = log_sections(tmp_path)
        assert body == output
        assert footer == "----- exit code 130 -----"

    def test_second_ctrl_c(self) -> None:
        """A second Ctrl+C, while the first is being reported, is ignored.

        This keeps it from interrupting the report with a traceback.
        """
        try:
            with pytest.raises(KeyboardInterrupt):
                _runs.stopped(signal.SIGINT, None)
            assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
        finally:
            signal.signal(signal.SIGINT, signal.default_int_handler)

    def test_ctrl_c_while_starting(self, tmp_path: Path) -> None:
        """Ctrl+C while pprl is starting stops it with its message and exit code 130.

        This happens before pprl can log, and without a traceback.
        """
        result = run_text(
            [
                sys.executable,
                "-c",
                (
                    "import signal, time\n"
                    "import pprl.cli\n"
                    "signal.raise_signal(signal.SIGINT)\n"
                    "time.sleep(10)"
                ),
            ],
            capture_output=True,
            cwd=tmp_path,
            timeout=60,
        )
        assert result.returncode == 130, result
        assert shows(msg.INTERRUPTED, result.stdout + result.stderr), result
        assert "Traceback" not in result.stdout + result.stderr, result.stderr
        assert files_in(tmp_path) == [], "Nothing is written"

    def test_main_takes_ctrl_c(self, tmp_path: Path) -> None:
        """Once a command is running, the command reports and logs Ctrl+C itself."""
        _inspect_workspace(tmp_path)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            run_in(tmp_path, ["inspect", "hashes.csv"])
            assert signal.getsignal(signal.SIGINT) == _runs.stopped
        finally:
            signal.signal(signal.SIGINT, signal.default_int_handler)

    def test_library_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Python warning is shown and logged as a [WARN] line.

        The line gives the warning's kind and place, never its text.
        """
        _hash_workspace(tmp_path)

        def warns(_func: object, _configuration: object) -> int:
            warnings.warn("SHOULD_NOT_APPEAR", UserWarning, stacklevel=1)
            return 0

        monkeypatch.setattr("pprl._commands.run_command", warns)
        rc, output = run_in(tmp_path, ["hash"])
        assert rc == 0, output
        assert shows(msg.LIBRARY_WARNING, output), output
        assert "UserWarning" in output, output
        assert "SHOULD_NOT_APPEAR" not in output, output
        assert not style_problems(output, rejected=False), output
        [(_, _, body, _)] = log_sections(tmp_path)
        assert body == output


class _Terminal(io.StringIO):
    """A fake terminal for a bar to write to, which keeps each write."""

    def __init__(self) -> None:
        super().__init__()
        self.writes = []

    @override
    def write(self, text: str) -> int:
        self.writes.append(text)
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True


def _in_terminal(monkeypatch: pytest.MonkeyPatch, columns: int = 80) -> _Terminal:
    """Send output to a fake terminal ``columns`` wide, and return it."""
    terminal = _Terminal()
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setattr(
        _ui.shutil, "get_terminal_size", lambda: os.terminal_size((columns, 24))
    )
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    return terminal


def _step_words() -> list[str]:
    return [value for name, value in vars(msg).items() if name.startswith("STEP_")]


class TestProgress:
    """The progress bar, as shown and as logged."""

    def test_done_logged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """In a terminal, a command leaves one [DONE] line.

        The line is logged with its time, before the command's result. None of
        the bar's frames are logged.
        """
        _hash_workspace(tmp_path)
        monkeypatch.setattr(_ui, "_is_terminal", lambda _stream: True)
        monkeypatch.setattr(_ui, "_wrap_width", lambda _stream: None)  # no wrapping
        rc, output = run_in(tmp_path, ["hash"])
        assert rc == 0, output
        [(_, _, body, _)] = log_sections(tmp_path)
        lines = body.splitlines()
        done = [line for line in lines if line.startswith("[DONE]")]
        assert len(done) == 1, body
        assert re.fullmatch(r"\[DONE\] pprl hash \(\d\d:\d\d:\d\d\)", done[0])
        assert lines[lines.index(done[0]) + 1].startswith("[ OK ]")
        assert "\r" not in body
        assert "█" not in body
        assert "\x1b" not in body
        assert "░" in output

    def test_steps_logged_with_constant_words(self, tmp_path: Path) -> None:
        """Each step's time is logged once, by its words, even outside a terminal."""
        _hash_workspace(tmp_path)
        rc, output = run_in(tmp_path, ["hash"])
        assert rc == 0, output
        log = read_text(log_of(tmp_path))
        steps = re.findall(r"\[INFO\] step: (.*?) ·", log.split("pprl hash")[-1])
        assert steps == [
            msg.STEP_READING_CLEANED,
            msg.STEP_HASHING,
            msg.STEP_WRITING_HASHES,
        ]

    @pytest.mark.parametrize("columns", [70, 80, 200])
    def test_one_width(self, columns: int) -> None:
        """The line keeps one width for every step's words, and fits the terminal.

        This holds at any share done and any time.
        """
        widths = {
            len(strip_ansi(_ui._frame(share, words, spent, whole, columns)))
            for words in _step_words()
            for share in (0, 0.009, 0.01, 0.42, 0.999, 1)
            for spent in (0, 86, 3599.5, 360000)
            for whole in (None, 90, 360000)
        }
        assert len(widths) == 1
        assert widths.pop() < columns

    def test_width_at_80(self) -> None:
        """In an 80-column terminal the line is 78 columns, as designed."""
        line = strip_ansi(_ui._frame(0.42, msg.STEP_HASHING, 36, 90, 80))
        assert len(line) == 78
        assert line.startswith("[ 42%] ██████████░░░░░░░░░░░░░░  Hashing records   ")
        assert line.endswith("  00:00:36 / 00:01:30")

    def test_time_spent_rounds_up(self) -> None:
        """A command under a second does not read 00:00:00."""
        assert _ui._clock(0.003) == "00:00:01"
        assert _ui._clock(1.0) == "00:00:01"
        assert _ui._clock(1.2) == "00:00:02"

    def test_bar_full_only_when_done(self) -> None:
        """The filled cells round down, so a bar with work to follow is not full."""

        def filled(share: float) -> int:
            return strip_ansi(_ui._frame(share, msg.STEP_HASHING, 1, 2, 80)).count("█")

        assert filled(0.999) == _ui._BAR_WIDEST - 1
        assert filled(1) == _ui._BAR_WIDEST

    def test_short_run_whole_time(self) -> None:
        """Under a minute, the total time is rounded up to 5 seconds."""
        assert _ui._steadied(None, 2, 0.5) == 5
        assert _ui._steadied(None, 12, 3) == 15
        assert _ui._steadied(None, 84, 30) == 90

    def test_words_fit_their_column(self) -> None:
        """No step has more words than its column holds."""
        assert all(len(words) <= _ui._WORDS_WIDTH for words in _step_words())

    @pytest.mark.parametrize("columns", [59, 58, 30])
    def test_narrow_never_wraps(self, columns: int) -> None:
        """A terminal too narrow for the line cuts it off instead of wrapping.

        A wrapped line could not be redrawn.
        """
        line = strip_ansi(_ui._frame(0.5, msg.STEP_HASHING, 1, 2, columns))
        assert len(line) < columns

    def test_frames_whole(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each frame reaches the terminal in one write, along with the line clear.

        If they were separate writes, the line would be blank between them
        whenever the main thread kept Python busy, and the timer would flash.
        """
        terminal = _in_terminal(monkeypatch)
        with _ui.progress("hash", ((msg.STEP_HASHING, 1),)):
            _ui.add_work(10)
            for _ in range(3):
                _ui.advance()
                assert _ui._state.on_screen is not None
                _ui._state.on_screen.draw()
        frames = [w for w in terminal.writes if "█" in w or "░" in w]
        assert frames
        assert all(frame.startswith("\r\x1b[K") for frame in frames)

    def test_faked_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Driven by a fake clock through every step, the bar follows the clock.

        The line keeps one width on each redraw, the time spent matches the
        clock, and the bar never goes backward.
        """
        terminal = _in_terminal(monkeypatch)
        clock = [100.0]
        monkeypatch.setattr(_ui.time, "perf_counter", lambda: clock[0])
        steps = [(words, 1) for words in _step_words()]
        bar = _ui._Bar("hash", steps)
        bar.stream = terminal
        seen = []
        for words, _ in steps:
            if words != steps[0][0]:
                bar.step(words, 4)
            for _ in range(4):
                bar.advance(1)
                clock[0] += 30
                bar.draw()
                seen.append(strip_ansi(terminal.writes[-1]).removeprefix("\r\x1b[K"))
                assert words in seen[-1]
        assert len({len(line) for line in seen}) == 1
        shares = [int(line[1:4]) for line in seen]
        assert shares == sorted(shares)
        assert shares[-1] == 100
        assert seen[0].endswith("00:00:30 / --:--:--")  # under 1%
        times = [line[-19:].split(" / ") for line in seen]
        known = [(spent, whole) for spent, whole in times if whole != "--:--:--"]
        assert known
        assert all(spent <= whole for spent, whole in known)

    def test_whole_time_holds_steady(self) -> None:
        """A wavering estimate does not change the time shown.

        An estimate that wavers around a rounded value leaves it unchanged,
        but one that truly moves changes it.
        """
        shown = _ui._steadied(None, 88, 10)
        wavering = [91, 89, 90.5, 88, 92, 87, 90.1, 79] * 20
        assert {_ui._steadied(shown, e, 11) for e in wavering} == {90}
        assert _ui._steadied(shown, 120, 12) == 120
        assert _ui._steadied(shown, 40, 12) == 40

    def test_never_goes_back(self) -> None:
        """A total that turns out larger does not move the bar backward."""
        bar = _ui._Bar("clean", ((msg.STEP_CLEANING, 1),))
        bar.add_work(2)
        bar.advance(2)
        assert bar.share() == 1
        bar.add_work(2)
        assert bar.share() == 1

    def test_repeated_words_are_steps_in_turn(self) -> None:
        """Steps may share their words, as sweep's do, and are reached in turn."""
        bar = _ui._Bar(
            "sweep",
            ((msg.STEP_HASHING, 1), (msg.STEP_LINKING, 1), (msg.STEP_LINKING, 1)),
        )
        bar.step(msg.STEP_LINKING, 1)
        assert bar.share() == pytest.approx(1 / 3)
        bar.step(msg.STEP_LINKING, 1)
        assert bar.share() == pytest.approx(2 / 3)

    def test_plan_keeps_what_is_done(self) -> None:
        """Steps planned later share out the rest of the bar."""
        bar = _ui._Bar("link", ((msg.STEP_READING_HASHES, 2), (msg.STEP_COMPARING, 98)))
        bar.plan([(msg.STEP_COMPARING, 3), (msg.STEP_GROUPING, 1)])
        bar.step(msg.STEP_GROUPING, 0)
        assert bar.share() == pytest.approx(0.02 + 0.98 * 3 / 4)

    def test_inner_bar_moves_outer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A silenced command inside another advances the outer bar's step.

        It leaves no [DONE] line of its own.
        """
        terminal = _in_terminal(monkeypatch)
        with _ui.progress("sweep", ((msg.STEP_HASHING, 1), (msg.STEP_LINKING, 1))):
            outer = _ui._state.bar
            assert outer is not None
            with _ui.silenced(), _ui.progress("hash", ((msg.STEP_WRITING_HASHES, 1),)):
                _ui.add_work(2)
                _ui.advance()
                assert outer.share() == pytest.approx(0.25)
            assert outer.share() == pytest.approx(0.5)
            assert _ui._state.bar is outer
        done = [strip_ansi(w) for w in terminal.writes if "[DONE]" in w]
        assert len(done) == 1
        assert "pprl sweep" in done[0]

    def test_line_shown_above_bar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A line shown during a run appears above the bar, which is drawn again."""
        terminal = _in_terminal(monkeypatch)
        with _ui.progress("hash", ((msg.STEP_HASHING, 1),)):
            _ui.note_user("Something to know.")
            text = "".join(terminal.writes)
            line = text.index("Something to know.")
            assert "\r\x1b[K" in text[:line]
            assert "░" in text[line:]  # The bar is drawn again below the line.

    @pytest.mark.usefixtures("capsys")
    def test_problem_erases_bar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A problem found by a command's checks erases the bar for good.

        No [DONE] line follows.
        """
        terminal = _in_terminal(monkeypatch)
        with _ui.progress("hash", ((msg.STEP_HASHING, 1),)):
            ctx = _ui.ValidationContext()
            ctx.fail("Secret", "No secret.")
            assert ctx.conclude()
            assert _ui._state.on_screen is None
        text = "".join(terminal.writes)
        assert "[DONE]" not in text
        assert text.endswith("\r\x1b[K")

    def test_raised_erases_bar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ctrl+C or a failure erases the bar, and no [DONE] line follows."""
        terminal = _in_terminal(monkeypatch)
        with (
            pytest.raises(KeyboardInterrupt),
            _ui.progress("hash", ((msg.STEP_HASHING, 1),)),
        ):
            raise KeyboardInterrupt
        assert _ui._state.on_screen is None
        assert _ui._state.bar is None
        text = "".join(terminal.writes)
        assert "[DONE]" not in text
        assert text.endswith("\r\x1b[K")

    def test_not_a_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Output that is not a terminal shows no bar and no [DONE] line."""
        terminal = _in_terminal(monkeypatch)
        monkeypatch.setattr(terminal, "isatty", lambda: False)
        with _ui.progress("hash", ((msg.STEP_HASHING, 1),)):
            assert _ui._state.on_screen is None
        assert terminal.writes == []

    def test_dumb_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TERM=dumb cannot redraw a line, so it counts as not a terminal."""
        terminal = _in_terminal(monkeypatch)
        monkeypatch.setenv("TERM", "dumb")
        with _ui.progress("hash", ((msg.STEP_HASHING, 1),)):
            pass
        assert terminal.writes == []

    def test_no_color(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NO_COLOR turns off every color, on the bar and every line."""
        terminal = _in_terminal(monkeypatch)
        monkeypatch.setenv("NO_COLOR", "1")
        with _ui.progress("hash", ((msg.STEP_HASHING, 1),)):
            _ui.ok_user(msg.WROTE_MERGED(count=2, output="merged"))
        assert not any(ANSI.search(write) for write in terminal.writes)

    def test_counts_bold_in_results_only(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A result's counts are bold on screen, but never in the log.

        Other lines' counts are not bold.
        """
        terminal = _in_terminal(monkeypatch)
        message = msg.WROTE_MERGED(count=1200, output="merged")
        assert ANSI.search(message) is None
        with _ui.run_log(tmp_path / "site_a.log", "site", "pprl test"):
            _ui.ok_user(message)
            _ui.warn_user(message)
        ok, warn = [w for w in terminal.writes if "merged" in w]
        assert "\x1b[1m1,200 merged tables\x1b[22m" in ok
        assert "\x1b[1m1,200" not in warn
        log = read_text(tmp_path / "site_a.log")
        assert "1,200 merged tables" in log
        assert "\x1b" not in log

    def test_writing_a_file_counts_every_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Writing a file moves the bar through all its work, one block at a time.

        It writes the same bytes as writing all at once would.
        """
        frame = pd.DataFrame(
            {"a": [str(i) for i in range(7)], "b": ["x, y", *"123456"]}
        )
        monkeypatch.setattr(_pprl_file, "RENDER_ROWS", 2)
        moved = []
        monkeypatch.setattr(_pprl_file, "advance", moved.append)
        path = tmp_path / "file.pprl"
        _pprl_file.write(
            path,
            "hashes",
            {"rows": frame, "empty": frame.iloc[:0]},
            provenance={"project": "p", "set": "s"},
        )
        assert sum(moved) == pytest.approx(2 * len(frame))
        assert len(moved) > 4
        _, _, compressed = path.read_bytes().partition(b"\n")
        whole = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
        data = zlib.decompress(compressed)
        assert struct.pack(">Q", len(whole)) + whole in data
        empty = frame.iloc[:0].to_csv(index=False, lineterminator="\n").encode("utf-8")
        assert data.endswith(struct.pack(">Q", len(empty)) + empty)

    def test_hashing_counts_records_not_chunks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bar counts records, not chunks.

        With a record salt, the number of chunks depends on the salt values,
        so showing it could reveal something about them.
        """
        moved = []
        monkeypatch.setattr(_hash, "advance", moved.append)
        results = [(["a", "b"], None), (["c"], None)]
        assert _hash._collected(results) == ["a", "b", "c"]
        assert moved == [2, 1]

    def test_color_codes_do_not_count_to_wrap(self) -> None:
        """A line is wrapped by its visible text, ignoring its color codes."""
        bold = "\x1b[1mthree\x1b[22m"
        assert _ui._wrapped(f"one two {bold} four", 14) == [
            f"one two {bold}",
            "four",
        ]
        assert _ui._fitted(f"[ OK ] one two {bold} four five six", 7, 30) == (
            f"[ OK ] one two {bold} four five\n       six"
        )


# Stands for text a user supplies: a folder name, a file name, or an export's
# column names.
CANARY = "Canary_Q7"


def _diag_patterns() -> list[re.Pattern[str]]:
    """Match each [INFO] line's message."""
    return [
        re.compile(_wording.template_pattern(message.text))
        for name, message in vars(msg).items()
        if name.startswith("INFO_") and isinstance(message, _wording.Message)
    ]


class TestWithheld:
    """What the log withholds."""

    def test_user_text_withheld(self, tmp_path: Path) -> None:
        """Text the user supplied is shown, but withheld from the log.

        Examples are a column name in an export, a file name typed on the
        command line, and an unknown command. The log, which is sent to
        others, has <withheld> in its place.
        """
        folder = tmp_path / "workspace"
        make_workspace(folder)
        write_text(
            folder / "patients.csv",
            f"person_id,name,city,state,zip,dob,{CANARY}\n"
            "1,Jo Surname,Boston,MA,02101,1990-01-01,x\n",
        )
        write_text(folder / f"{CANARY}.csv", f"a,{CANARY}\n1,2\n")
        write_text(folder / "spaced.csv", f"a, {CANARY}\n1,2\n")
        write_text(folder / "twice.csv", f"{CANARY},{CANARY}\n1,2\n")
        (folder / f"{CANARY}.dat").write_bytes(_pprl_file.MAGIC + b"damaged\n")
        for argv in (
            ["clean"],
            ["inspect", f"{CANARY}.csv"],
            ["inspect", f"./{CANARY}.csv"],
            ["inspect", f"missing_{CANARY}.csv"],
            ["inspect", f"/{CANARY}.csv"],
            ["inspect", "spaced.csv"],
            ["inspect", "twice.csv"],
            ["inspect", f"{CANARY}.dat"],
            [CANARY],
            ["hash", CANARY],
        ):
            before = len(log_sections(folder))
            _rc, output = run_in(folder, argv)
            assert CANARY in output, f"{argv} shows what the user supplied"
            sections = log_sections(folder)
            assert len(sections) == before + 1, argv
            header, _, body, _ = sections[-1]
            assert CANARY not in header + body, f"{argv} logged:\n{header}\n{body}"
            assert _wording.WITHHELD in header + body, argv
            assert body == output, "Otherwise, the log is what was shown"
        assert CANARY not in read_text(log_of(folder))

    def test_hashing_details_withheld(self, tmp_path: Path) -> None:
        """No log reveals how a site hashes.

        Hash logs none of its settings. Inspect shows them, but they are
        withheld from the log.
        """
        folder = tmp_path / "workspace"
        make_workspace(
            folder,
            xor_folds=3,
            project_salt=CANARY,
            record_salt={"field": "zip", "length": 3},
        )
        assert synthesize_patients(folder, n=30) == 0
        rc, output = run_in(folder, ["hash"])
        assert rc == 0, output
        rc, output = run_in(folder, ["inspect", "outbox/linkage/site_a.hashes.pprl"])
        assert rc == 0, output
        assert "1024" in output, "inspect shows the hash's bits"
        rc, output = run_in(folder, ["inspect", "site_a.pprl"])
        assert rc == 0, output
        assert CANARY in output, "inspect shows the bundle's settings"
        assert "record_salt" in output, output
        log = read_text(log_of(folder))
        for detail in (
            CANARY,
            "record_salt",
            "xor_folds",
            "XOR",
            "density",
            "weight",
            "hash_length",
            "fill_ratio",
        ):
            assert detail not in log, f"{detail} logged:\n{log}"

    def test_bad_hashing_settings_withheld(self, tmp_path: Path) -> None:
        """A wrong setting is shown, but its value is withheld from the log."""
        folder = tmp_path / "coordinator"
        make_workspace(folder, role="coordinator")
        write_yaml(
            folder / "project.yml",
            PROJECT_SETTINGS
            | {
                "project_salt": 918273,
                "record_salt": {"field": CANARY},
                "xor_folds": -918273,
                "hash_length": 918272,
                "fill_ratio": 918.273,
            },
        )
        rc, output = run_in(folder, ["bundles"])
        assert rc == 1
        assert CANARY in output, output
        assert "918273" in output, output
        log = read_text(log_of(folder))
        assert CANARY not in log, log
        assert "918273" not in log, log
        assert "918272" not in log, log
        assert "918.273" not in log, log

    @pytest.mark.parametrize(
        ("setting", "value", "words"),
        [
            ("xor_folds", -918273, "`xor_folds` should be"),
            ("project_salt", 918273, "`project_salt` should be"),
            ("record_salt", {"field": "zip", "length": -918273}, "`record_salt`"),
            ("record_salt", {"field": CANARY}, "not one of `fields`"),
        ],
    )
    def test_bad_bundle_hashing_settings(
        self, tmp_path: Path, setting: str, value: object, words: str
    ) -> None:
        """Hash checks a bundle's hashing settings, and withholds them from the log.

        A bundle that pprl bundles wrote always passes, but one changed by
        hand must still stop hash with a message rather than an error.
        """
        folder = tmp_path / "workspace"
        make_workspace(folder)
        write_bundle(folder, **{setting: value})
        assert synthesize_patients(folder, n=30) == 0
        rc, output = run_in(folder, ["hash"])
        assert rc == 1, output
        assert words in output, output
        assert "Traceback" not in output, output
        log = read_text(log_of(folder))
        assert CANARY not in log, log
        assert "918273" not in log, log
        assert not list((folder / "outbox" / "linkage").glob("*.hashes.pprl"))


class TestDiagnostics:
    """The [INFO] lines every run logs."""

    def test_every_run(self, tmp_path: Path) -> None:
        """Every run logs how it ran, in lines that are never shown.

        The lines give how pprl was installed, its packages, the computer,
        each file it read, how long each step took, and its peak memory. Each
        line is one of the catalog's messages.
        """
        _hash_workspace(tmp_path)
        rc, output = run_in(tmp_path, ["hash"])
        assert rc == 0, output
        info_lines = log_info_lines(tmp_path)[-1]
        kinds = [line.split(":")[0] for line in info_lines]
        assert kinds[:3] == ["installed", "packages", "computer"], info_lines
        assert kinds[-1] == "finished", info_lines
        assert any(line.startswith("read site_a.clean.pprl: ") for line in info_lines)
        assert any(line.startswith("step: Hashing") for line in info_lines)
        patterns = _diag_patterns()
        for line in info_lines:
            assert any(p.fullmatch(line) for p in patterns), line
            assert line not in output, "An [INFO] line is never shown"
        assert msg.INFO_TAG not in output

    def test_csv_read(self, tmp_path: Path) -> None:
        """A CSV file that was read is logged by its shape, never its column names.

        The log gives its size, shape, encoding and line endings.
        """
        make_workspace(tmp_path)
        (tmp_path / "patients.csv").write_bytes(
            "﻿person_id,name,city,state,zip,dob\r\n"
            "1,Jo Surname,Boston,MA,02101,1990-01-01\r\n".encode()
        )
        run_in(tmp_path, ["clean"])
        [line] = [
            line
            for line in log_info_lines(tmp_path)[-1]
            if line.startswith("read patients.csv")
        ]
        assert "1 row · 6 columns · UTF-8 with a byte-order mark · Windows" in line
        assert "Surname" not in line
        assert "dob" not in line

    def test_crash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A crash is logged with where it happened, never with its text.

        The log gives the places in pprl's code it passed through, and the
        place in the library where it was raised.
        """
        _hash_workspace(tmp_path)

        def broken(*_args: object, **_kwargs: object) -> Never:
            reason = f"{CANARY} was in the data"
            raise ValueError(reason)

        monkeypatch.setattr("pprl._pprl_file.zlib.compressobj", broken)
        rc, output = run_in(tmp_path, ["hash"])
        assert rc == 1, output
        assert shows(msg.UNEXPECTED, output), output
        [crash] = [
            line for line in log_info_lines(tmp_path)[-1] if line.startswith("crash:")
        ]
        assert crash.startswith("crash: ValueError at pprl/"), crash
        assert "pprl/_pprl_file.py, line " in crash, crash
        assert CANARY not in crash
        assert str(tmp_path) not in crash

    def test_never_stops_a_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A detail that can't be found is logged as unknown; the run goes on."""

        def broken(*_args: object) -> Never:
            reason = "no such call on this computer"
            raise OSError(reason)

        for name in ("install_method", "packages", "total_memory", "peak_memory"):
            monkeypatch.setattr(_system, name, broken)
        _hash_workspace(tmp_path)
        rc, output = run_in(tmp_path, ["hash"])
        assert rc == 0, output
        info_lines = log_info_lines(tmp_path)[-1]
        assert "installed: unknown" in info_lines, info_lines
        assert info_lines[-1].endswith("peak memory unknown"), info_lines

    @pytest.mark.skipif(sys.platform != "linux", reason="/proc is Linux's")
    def test_peak_memory_own(self) -> None:
        """On Linux, peak memory is this process's own (its VmHWM).

        It is not the peak of the process that started it, which getrusage
        would report.
        """
        peak = _system._own_peak()
        assert peak, peak
        assert 10 * 1024**2 < peak < 64 * 1024**3, peak
        total = _system.peak_memory()
        assert total is not None
        assert total >= peak


class _Installed:
    """A fake distribution's metadata files, for install_method."""

    def __init__(self, **files: str) -> None:
        self.files = files

    def read_text(self, name: str) -> str | None:
        return self.files.get(name)


class TestInstallMethod:
    """How pprl was installed, as the log records it."""

    @pytest.mark.parametrize(
        ("receipt", "files", "kind", "source"),
        [
            (
                True,
                {
                    "INSTALLER": "uv",
                    "direct_url.json": '{"url": "https://github.com/TuftsCTSI/pprl/'
                    'releases/download/v3.0.0/pprl-3.0.0-py3-none-any.whl", '
                    '"archive_info": {}}',
                },
                "uv tool",
                "release wheel",
            ),
            (
                False,
                {
                    "INSTALLER": "pip",
                    "direct_url.json": '{"url": "file:///home/jdoe/pprl-3.0.0-py3-'
                    'none-any.whl", "archive_info": {}}',
                },
                "pip",
                "local wheel",
            ),
            (
                False,
                {
                    "INSTALLER": "uv",
                    "direct_url.json": '{"url": "file:///home/jdoe/pprl", '
                    '"dir_info": {"editable": true}}',
                },
                "uv",
                "editable",
            ),
            (
                False,
                {
                    "INSTALLER": "conda",
                    "direct_url.json": '{"url": "https://mirror.example.org/w/'
                    'pprl-3.0.0-py3-none-any.whl", "archive_info": {}}',
                },
                "other",
                "remote wheel",
            ),
            (False, {}, "unknown", "index"),
        ],
    )
    def test_detected(
        self, tmp_path: Path, *, receipt: bool, files: dict, kind: str, source: str
    ) -> None:
        """How pprl was installed is detected, and logged without any local path.

        It is detected from uv's receipt and the installer's records.
        """
        if receipt:
            write_text(tmp_path / "uv-receipt.toml", "")
        method = _system.install_method(tmp_path, _Installed(**files))
        assert (method["kind"], method["source"]) == (kind, source), method
        assert "jdoe" not in repr(method), "No path on the computer"
