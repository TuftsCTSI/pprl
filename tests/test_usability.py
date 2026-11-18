"""Test usability.

Every kind of user input runs through the CLI and is either accepted or
rejected with a message the user can act on. The cases are defined in one
module, and the grid of inputs and commands they fill in another.
`make docs` (or `python -m tests.usability_cases`) writes both into a table
for review.

Every message comes from the message catalog, and every case's output
follows the same rules (see ``style_problems``).
"""

import io
import os
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from pprl import _hash, _pprl_file, _ui, _wording, cli
from pprl import _messages as msg
from pprl._io import read_csv_frame, read_text, write_text
from tests import message_catalog
from tests.helpers import (
    HASH_SIZES,
    SYNTH_FIELDS,
    SYNTH_SECRET,
    assert_terminal_output_matches,
    call_kwargs,
    log_sections,
    provenance,
    run_scenario,
    shows,
    strip_ansi,
    write_cleaned,
)
from tests.usability_cases import CASES
from tests.usability_grid import build_inputs, run_case

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tests.usability_grid import Case

pytestmark = pytest.mark.usability


@pytest.fixture(scope="module")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the valid files that every case starts from (see build_inputs)."""
    folder = tmp_path_factory.mktemp("usability") / "inputs"
    build_inputs(folder)
    return folder


def _case_id(case: Case) -> str:
    return f"{case.command}: {case.did}"


# Text showing that a message leaked Python or library internals.
_INTERNALS = ("NoneType", "got: None", "Traceback", "C error", "tokenizing", "['", "{'")
_TAG = re.compile(r"^\[ ?(OK|NOTE|Y/N\?|WARN|STOP|FAIL) ?\] ")


def _messages(output: str) -> list[tuple[str, str]]:
    """Split an output into its tagged messages.

    Each message is (tag, text including continuation lines).
    """
    messages = []
    for line in output.splitlines():
        tag = _TAG.match(line)
        if tag:
            messages.append([tag.group(1), line])
        elif messages and line.startswith(" "):
            messages[-1][1] += "\n" + line
    return [(tag, text) for tag, text in messages]


def _logged(case: Case) -> bool:
    """Tell whether a case's run is logged.

    Every run is logged except pprl on its own, help and the version.
    """
    argv = case.argv if case.argv is not None else (case.command,)
    return not (not argv or any(a in ("-h", "--help", "--version") for a in argv))


def log_problems(case: Case, result: object, output: str, folder: Path) -> list[str]:
    """Find every way a case's log differs from what the run showed.

    Return the differences as sentences. A logged run must be one section,
    whose body is the output, line for line, and whose footer gives the exit
    code.
    """
    sections = log_sections(folder)
    if not _logged(case):
        return [f"logged {len(sections)} run(s)"] if sections else []
    if len(sections) != 1:
        return [f"logged {len(sections)} run(s), not 1"]
    header, system, body, footer = sections[0]
    problems = []
    argv = [a.replace("{workspace}", str(folder)) for a in case.argv or (case.command,)]
    # Every word that is not a known command or option is withheld.
    command = _wording.logged(cli.command_line("pprl", argv, cli._COMMANDS))
    if not header.endswith(f" · {command} ====="):
        problems.append(f"the header does not name the command: {header!r}")
    if not system or " · Python " not in system:
        problems.append(f"the second line is not the system: {system!r}")
    if body != output:
        problems.append(f"the log's body differs from the output:\n{body}")
    if footer != f"----- exit code {result} -----":
        problems.append(f"the footer is {footer!r}, not exit code {result}")
    return problems


def style_problems(
    output: str, *, rejected: bool, shows_paths: Iterable[str] = ()
) -> list[str]:
    """List every way an output breaks the rules for messages, as sentences.

    * no full path, except one the user gave (``shows_paths``)
    * no Python or library internals, such as "NoneType" or "['"
    * every problem (FAIL, STOP, WARN) has a fix, except the summary that
      ends a rejected run
    * a rejected run ends with a [STOP] message
    """
    problems = [f"shows {fragment!r}" for fragment in _INTERNALS if fragment in output]
    problems.extend(
        f"shows the full path {path}"
        for path in re.findall(r"(?<![\w.])/[\w./-]*workspace[\w./-]*", output)
        if not any(path.startswith(given) for given in shows_paths)
    )
    messages = _messages(output)
    summary = msg.NOTHING_WRITTEN()
    for tag, text in messages:
        if (
            tag in ("FAIL", "STOP", "WARN")
            and summary not in text
            and _wording.FIX_PREFIX not in text
        ):
            problems.append(f"has no fix: {text!r}")
    if rejected and (not messages or messages[-1][0] != "STOP"):
        problems.append("a rejected run does not end with a [STOP] message")
    return problems


class TestUsability:
    """What users see, for every kind of input."""

    @pytest.mark.parametrize("case", CASES, ids=_case_id)
    def test_input(self, case: Case, inputs: Path, tmp_path: Path) -> None:
        """Each kind of input is accepted or rejected as documented.

        Any message follows the rules for messages.
        """
        result, output = run_case(case, inputs, tmp_path)
        if case.crashes:
            assert type(result).__name__ == case.crashes, (
                f"Expected a {case.crashes}, got {result!r}\n{output}"
            )
            output = str(result)
        else:
            assert not isinstance(result, Exception), f"Crashed: {result!r}"
            expected = 0 if case.accepted else 1
            assert result == expected, (
                f"Expected exit code {expected}, got {result}\n{output}"
            )
        for says in case.says_all:
            assert shows(says, output), f"Expected {says!r} in:\n{output}"
        # pprl on its own prints the help and exits with 1, but reports no problem.
        rejected = result == 1 and case.argv != ()
        # A full path may be shown only if the user gave one.
        gave_paths = "{workspace}" in repr(case.config) + repr(case.argv)
        shows_paths = (str(tmp_path),) if gave_paths else ()
        problems = style_problems(output, rejected=rejected, shows_paths=shows_paths)
        assert not problems, "\n".join(problems) + f"\nin:\n{output}"
        if case.creates:
            assert (Path(tmp_path) / "workspace" / case.creates).exists(), (
                f"{case.creates} should have been written"
            )
        if not case.crashes:
            problems = log_problems(case, result, output, tmp_path / "workspace")
            assert not problems, "\n".join(problems) + f"\nin:\n{output}"

    def test_all_errors_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Independent validation errors are all reported together."""
        bad = pd.DataFrame({"study_id": ["1", "1"], "wrong_col": ["v", "v2"]})
        _pprl_file.write(
            tmp_path / "site_a.clean.pprl",
            "clean",
            {"patients": bad, "study_ids": bad[["study_id"]]},
            provenance=provenance("site_a"),
        )
        write_text(tmp_path / "existing.hashes.pprl", "already here\n")

        rc = _hash.hash_records(
            patients="site_a.clean.pprl",
            secret=SYNTH_SECRET,
            fields=SYNTH_FIELDS,
            output="existing.hashes.pprl",
            **HASH_SIZES,
            provenance=provenance("site_a"),
            **call_kwargs(tmp_path),
        )
        assert rc == 1, "Multiple validation failures should return 1"

        errors = capsys.readouterr().err
        for message, what in (
            (msg.OUTPUT_EXISTS, "Existing output"),
            (msg.DUPLICATE_IDS, "Duplicate study_ids"),
            (msg.MISSING_COLUMNS, "Missing fields"),
            (msg.UNEXPECTED_COLUMNS, "Extra columns"),
        ):
            assert shows(message, errors), f"{what} should be reported"
        assert errors.rstrip().endswith(msg.NOTHING_WRITTEN()), (
            "The table should end by saying that nothing was written"
        )

    def test_terminal_layout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The displayed output is the same on every platform."""
        scenario = {
            "seed": 42,
            "sites": ["site_a"],
            "outputs": ["patients"],
            "generated_individuals": [{"sites": ["site_a"], "count": 5}],
        }
        rc = run_scenario(tmp_path, scenario, "terminal")
        assert rc == 0
        assert_terminal_output_matches(capsys.readouterr().out, "terminal_synth")
        patients = read_csv_frame(tmp_path / "terminal" / "patients.csv")
        write_cleaned(patients, tmp_path / "site_a.clean.pprl", "site_a")

        rc = _hash.hash_records(
            patients="site_a.clean.pprl",
            secret=SYNTH_SECRET,
            fields=SYNTH_FIELDS,
            output="site_a.hashes.pprl",
            **HASH_SIZES,
            provenance=provenance("site_a"),
            **call_kwargs(tmp_path),
        )
        assert rc == 0
        assert_terminal_output_matches(capsys.readouterr().out, "terminal_hash")

        rc = _hash.hash_records(
            patients="nonexistent.clean.pprl",
            secret=SYNTH_SECRET,
            fields=SYNTH_FIELDS,
            output="site_a.hashes.pprl",
            **HASH_SIZES,
            provenance=provenance("site_a"),
            **call_kwargs(tmp_path),
        )
        assert rc == 1
        assert_terminal_output_matches(
            capsys.readouterr().err, "terminal_validation_errors"
        )


class _Terminal(io.StringIO):
    """Output that reports being a terminal, as a user's does."""

    def isatty(self) -> bool:
        return True


class TestLayout:
    """How messages are laid out."""

    def test_tags_one_width(self) -> None:
        """Every tag is the same width.

        This way every message's text starts at the same column.
        """
        for word in _ui._TAGS:
            tag = strip_ansi(_ui._tag(word))
            assert len(tag) == _ui.TEXT_COLUMN, repr(tag)
            assert re.fullmatch(r"\[[ A-Z/?]{4}\] ", tag), repr(tag)

    def test_wrapped_in_terminal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In a terminal, a long message is wrapped at the terminal's width.

        Each wrapped line starts at the text column. The log keeps the message
        as one line.
        """
        terminal = _Terminal()
        monkeypatch.setattr(sys, "stdout", terminal)
        monkeypatch.setattr(
            shutil, "get_terminal_size", lambda *_: os.terminal_size((60, 24))
        )
        text = "word " * 40
        with _ui.run_log(tmp_path / "linkage.log", "linkage agent", "pprl link"):
            _ui.warn_user(_wording.Message(text.strip(), "Do this.")())
        shown = strip_ansi(terminal.getvalue()).splitlines()
        assert len(shown) > 2, shown
        assert all(len(line) <= 60 for line in shown), shown
        assert all(line.startswith(" " * _ui.TEXT_COLUMN) for line in shown[1:])
        logged = read_text(tmp_path / "linkage.log").splitlines()
        assert f"[WARN] {text.strip()}" in logged, logged

    def test_not_wrapped_elsewhere(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Output that is not a terminal keeps one line per line of the message.

        This covers output to a file or a pipe.
        """
        _ui.ok_user("word " * 40)
        assert len(capsys.readouterr().out.splitlines()) == 1

    def test_plurals(self) -> None:
        """No message shows "(s)".

        A count is written with its noun in the singular or plural, and with
        thousands separators.
        """
        assert msg.WROTE_HASHES(count=1, site="site_a", output="x") == (
            "Wrote 1 hash from site_a to x."
        )
        assert "1,200 rows" in msg.SELECT_TABLE_REPORT(
            table="scores", kept=1, rows=1200
        )
        assert "rows 3, 4 (" in msg.BLANK_ROWS(name="x", rows=[3, 4])
        assert "row 3 (" in msg.BLANK_ROWS(name="x", rows=[3])

    def test_fixes_for_users(self) -> None:
        """Every fix that pprl can show its users is one they can follow.

        A fix is either something they can do, in plain words, or a request to
        send the log to the coordinator. It never asks them to change a
        setting, read a developer document, or work out what is wrong with
        another role's files.
        """
        problems = message_catalog.fix_problems()
        assert not problems, "Fixes not written for users:\n" + "\n".join(
            f"  {problem}" for problem in problems
        )

    def test_settings_fix_by_audience(self) -> None:
        """A problem with the project's settings has a fix for each audience.

        pprl's users are asked to send the log. The user of the commands on the
        project is told how to correct the settings.
        """
        message = msg.COLUMN_NO_RULE
        assert message.kind == _wording.SETTINGS
        assert _wording.ESCALATE in message(column="score")
        with _wording.for_settings_writer():
            shown = message(column="score")
        assert _wording.ESCALATE not in shown
        assert "`rank_by`" in shown
