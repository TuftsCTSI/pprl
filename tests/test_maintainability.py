"""Test code and test-suite quality."""

import ast
import fnmatch
import functools
import io
import re
import shlex
import shutil
import sys
from contextlib import redirect_stdout
from typing import TYPE_CHECKING

import pytest

from pprl import cli
from pprl._io import for_reading, read_text, write_text
from tests import message_catalog, pins
from tests import test_reliability as reliability
from tests.commands import DESTROY_YES, run_text
from tests.helpers import (
    OBJECTIVES,
    RECEIPT_GLOB,
    SOURCE_ROOT,
    TEST_DIR,
    assert_text_matches,
    python_files,
    run_in,
)
from tests.usability_cases import CASES
from tests.usability_grid import grid_problems

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.maintainability

# The places where pprl's code may show text without going through the UI
# module (which logs everything): the help text (pprl on its own, which is not
# logged), the stream setup, and Ctrl+C while pprl starts, before the UI module
# can be imported and before any log is open.
OUTPUT_ALLOWED = {
    ("cli.py", "_parse_and_run", "print"),
    ("_runs.py", "prepare_terminal", "sys.stdout"),
    ("_runs.py", "prepare_terminal", "sys.stderr"),
    ("_starting.py", "stopped_while_starting", "sys.stdout"),
    ("_starting.py", "stopped_while_starting", "sys.stderr"),
}


def _output(node: ast.AST) -> str | None:
    """Name the kind of output that ``node`` produces.

    Return "print", "sys.stdout" (or another stream), "logging", or None.
    """
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
        return "print"
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        and node.attr in ("stdout", "stderr", "__stdout__", "__stderr__")
    ):
        return f"sys.{node.attr}"
    if (
        isinstance(node, ast.Import)
        and any(alias.name.split(".")[0] == "logging" for alias in node.names)
    ) or (
        isinstance(node, ast.ImportFrom) and (node.module or "").startswith("logging")
    ):
        return "logging"
    return None


def output_bypassing_ui() -> list[str]:
    """Find every place in pprl, outside the UI module, that shows output itself.

    That means printing, using sys.stdout or sys.stderr, or using logging.
    Each is reported as "file:line: what (in function)".
    """
    problems = []
    for path in python_files("src/pprl"):
        if path.name == "_ui.py":
            continue
        tree = ast.parse(read_text(path), filename=str(path))
        functions = {}  # Each node's innermost function, keyed by node id
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(function):
                    functions[id(node)] = function.name
        for node in ast.walk(tree):
            what = _output(node)
            if what is None or not isinstance(node, (ast.expr, ast.stmt)):
                continue
            function = functions.get(id(node), "<module>")
            if (path.name, function, what) not in OUTPUT_ALLOWED:
                relative = path.relative_to(SOURCE_ROOT)
                problems.append(f"{relative}:{node.lineno}: {what} (in {function})")
    return problems


# By default, Python reads and writes text in the computer's own encoding
# (cp1252 on Windows), but pprl's files, code and docs are UTF-8. So only
# these helpers may read or write text, and each uses UTF-8: read_text,
# write_text, for_reading, for_writing, for_appending and read_strings_csv
# (for a CSV file pprl wrote) in pprl's I/O module, and run_text in the tests'
# commands module (for a command's output).
_TEXT_HELPERS = {
    "src/pprl/_io.py": {
        "for_reading",
        "for_writing",
        "for_appending",
        "read_strings_csv",
    },
    "tests/commands.py": {"run_text"},
}

# The make files. Their recipes may run Python only as repository modules
# (python -m), never as inline code (python -c), because the text check
# could not read inline code.
_MAKE_FILES = ("Makefile",)


def text_io_outside_helpers() -> list[str]:
    """Find every call in pprl or the tests that reads or writes text itself.

    Calls through the helpers above are allowed. Each problem is reported as
    "file:line: call".
    """
    problems = []
    for path in python_files("src/pprl", "tests"):
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        allowed = _TEXT_HELPERS.get(relative, set())
        tree = ast.parse(read_text(path), filename=str(path))
        for function in ast.walk(tree):
            functions = (ast.FunctionDef, ast.AsyncFunctionDef)
            if not isinstance(function, (*functions, ast.Module)):
                continue
            if isinstance(function, functions) and function.name in allowed:
                continue
            nodes = list(ast.iter_child_nodes(function))
            handles = {
                item.optional_vars.id
                for node in nodes
                for inner in _outside_functions(node)
                if isinstance(inner, (ast.With, ast.AsyncWith))
                for item in inner.items
                if isinstance(item.optional_vars, ast.Name)
            }
            for node in nodes:
                problems.extend(
                    f"{relative}:{call.lineno}: {ast.unparse(call)[:60]}"
                    for call in _outside_functions(node)
                    if isinstance(call, ast.Call) and _handles_text(call, handles)
                )
    for name in _MAKE_FILES:
        for number, line in enumerate(read_text(SOURCE_ROOT / name).splitlines(), 1):
            if re.search(r"\bpython3? -c\b", line):
                problems.append(f"{name}:{number}: {line.strip()[:60]}")
    return sorted(set(problems))


def _outside_functions(node: ast.AST) -> Iterator[ast.AST]:
    """Walk ``node`` and the nodes inside it, skipping functions defined inside it.

    Each function is checked separately.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _outside_functions(child)


def _handles_text(node: ast.Call, handles: set[str]) -> bool:
    """Tell whether a call reads or writes text.

    These calls count: a Path's read_text or write_text; open (built-in or
    a Path's) in a text mode; a command run with its output as text; and
    pandas reading or writing a CSV file by its name. pandas using a handle
    in ``handles`` does not count. Those are handles the function opened
    with ``with``, and their open call is checked separately.
    """
    keywords = {k.arg: k.value for k in node.keywords}
    if None in keywords:
        return False  # Passes on its own caller's arguments (**kwargs)
    text = keywords.get("text")
    if isinstance(text, ast.Constant) and text.value:
        return True
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in ("to_csv", "read_csv"):
        # to_csv() with no file returns the text. Given a file name, pandas
        # opens the file itself, out of the helpers' sight (and to_csv writes
        # the computer's own line endings).
        file = node.args[0] if node.args else None
        file = keywords.get("path_or_buf", keywords.get("filepath_or_buffer", file))
        return file is not None and not (
            isinstance(file, ast.Name) and file.id in handles
        )
    if name in ("read_text", "write_text"):
        # Only a Path's method counts: not pprl's own helper (a plain name),
        # and not a Distribution's read_text, which takes a file name.
        return isinstance(func, ast.Attribute) and (
            name == "write_text" or not node.args
        )
    if name != "open" or getattr(getattr(func, "value", None), "id", None) == "os":
        return False  # os.open opens a file descriptor, not text
    modes = [k.value for k in node.keywords if k.arg == "mode"]
    position = 0 if isinstance(func, ast.Attribute) else 1  # Path.open or builtin
    modes += node.args[position : position + 1]
    mode = modes[0] if modes else ast.Constant("r")
    return not (isinstance(mode, ast.Constant) and "b" in str(mode.value))


def _tracked_files() -> list[str]:
    """List every file git tracks, as a path from the repository's root."""
    result = run_text(["git", "ls-files"], cwd=SOURCE_ROOT, capture_output=True)
    return sorted(
        name for name in result.stdout.splitlines() if (SOURCE_ROOT / name).is_file()
    )


# The tracked text files that the whitespace check leaves alone: Python (ruff
# format checks it, and a string in it may hold any whitespace on purpose),
# CSV data, the stored outputs (pprl wrote them, and they must match what it
# writes), and the OMOP CDM files (copied unchanged from their source).
_WHITESPACE_SKIPPED = ("*.py", "*.csv", "tests/expected/*", "src/pprl/synth/cdm/*")

# Make requires a tab at the start of every recipe line.
_TABS_ALLOWED = ("Makefile",)


def text_layout_problems() -> list[str]:
    r"""Find whitespace and encoding faults in the tracked text files.

    Each file must be UTF-8 without a byte order mark, have \n line endings,
    no trailing spaces or tabs, no tabs at all (but in a make file), and end
    with a single \n (an empty file is allowed). Each problem is reported as
    "file:line: fault", or "file: fault" for the whole file.
    """
    problems = []
    for name in _tracked_files():
        if any(fnmatch.fnmatch(name, pattern) for pattern in _WHITESPACE_SKIPPED):
            continue
        try:
            with for_reading(SOURCE_ROOT / name, newline="") as fh:
                text = fh.read()
        except UnicodeDecodeError:
            problems.append(f"{name}: not UTF-8")
            continue
        problems.extend(_layout_faults(name, text))
    return problems


def _layout_faults(name: str, text: str) -> Iterator[str]:
    """Report the faults in ``text``, the contents of the file ``name``.

    ``text`` was read with its line endings unchanged.
    """
    if text.startswith("﻿"):
        yield f"{name}: starts with a byte order mark"
    if text and not text.endswith("\n"):
        yield f"{name}: does not end with a newline"
    elif text.endswith("\n\n"):
        yield f"{name}: ends with blank lines"
    for number, line in enumerate(text.split("\n"), 1):
        content = line.rstrip("\r")
        if content != line or "\r" in content:
            yield f"{name}:{number}: carriage return"
        if content != content.rstrip(" \t"):
            yield f"{name}:{number}: trailing whitespace"
        if "\t" in content.rstrip(" \t") and name not in _TABS_ALLOWED:
            yield f"{name}:{number}: tab"


# A step's action, as a workflow names it: "uses: owner/repo@ref # comment".
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<action>[^\s#]+)\s*(?:#\s*(?P<note>.*))?$")
# A full commit SHA, which (unlike a tag or branch) its owner cannot move.
_COMMIT = re.compile(r"[0-9a-f]{40}")


def unpinned_actions() -> list[str]:
    """Find every action the workflows use without pinning it to one commit.

    An action from another repository must name a full commit SHA, followed by
    a comment giving the version it is (``@<sha> # v1.2.3``), so that a moved
    tag cannot change what CI runs. An action in this repository (``./``) is
    allowed. Each problem is reported as "file:line: action".
    """
    problems = []
    workflows = [
        name
        for name in _tracked_files()
        if name.startswith(".github/") and name.endswith((".yml", ".yaml"))
    ]
    for name in workflows:
        for number, line in enumerate(read_text(SOURCE_ROOT / name).splitlines(), 1):
            if not (uses := _USES.match(line)):
                continue
            action = uses.group("action")
            if action.startswith("./"):
                continue
            ref = action.partition("@")[2]
            if not _COMMIT.fullmatch(ref):
                problems.append(f"{name}:{number}: {action} is not pinned to a commit")
            elif not uses.group("note"):
                problems.append(f"{name}:{number}: {action} does not name its version")
    return problems


# A make rule's first line, "target: prerequisites" (not "name := value").
_MAKE_RULE = re.compile(r"^(?P<target>[A-Za-z0-9_.-]+)\s*:(?!=)")


def makefile_problems() -> list[str]:
    """Find the Makefile's targets that are not declared or not documented.

    Every target must be in .PHONY (none of them makes a file of its name),
    and every target but help itself must be shown by ``make help`` as
    "make <target>". Equally, .PHONY and help must name no target that does
    not exist.
    """
    targets, phony, helped = [], set(), set()
    in_help = False
    for line in read_text(SOURCE_ROOT / "Makefile").splitlines():
        if line.startswith("\t"):
            if in_help:
                helped.update(re.findall(r"\bmake ([A-Za-z0-9_-]+)", line))
            continue
        in_help = False
        if not (rule := _MAKE_RULE.match(line)):
            continue
        target = rule.group("target")
        if target == ".PHONY":
            phony.update(line.split(":", 1)[1].split())
        elif not target.startswith("."):
            targets.append(target)
            in_help = target == "help"
    shown = set(targets) - {"help"}
    return [
        *(f"{t} is not in .PHONY" for t in targets if t not in phony),
        *(f"{t} is not shown by make help" for t in sorted(shown - helped)),
        *(f".PHONY names {t}, which is no target" for t in sorted(phony - {*targets})),
        *(f"make help shows {t}, which is no target" for t in sorted(helped - shown)),
    ]


def _terminal_fixtures() -> set[str]:
    """Name the stored terminal outputs the tests compare with.

    Each is the last argument, a string, of a call to
    assert_terminal_output_matches, and is stored as expected/<name>.txt.
    """
    names = set()
    for path in python_files("tests"):
        for node in ast.walk(ast.parse(read_text(path), filename=str(path))):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "assert_terminal_output_matches"
                and node.args
                and isinstance(node.args[-1], ast.Constant)
            ):
                names.add(f"{node.args[-1].value}.txt")
    return names


def fixture_problems() -> list[str]:
    """Find the stored outputs no test compares with, and those missing.

    Each tracked file in tests/expected belongs to one stored output: a file
    at the top, or a folder in synth/, examples/ or guides/ (the test that
    compares a folder checks that it holds exactly the files it should). The
    outputs the tests use come from the same names the tests use: the
    reliability cases, the example scenarios, and the terminal outputs.
    """
    used = {
        "same_hashes.csv",
        "guides",
        *(f"link_{case}.csv" for case in reliability.CASES),
        *(f"synth/{case}" for case in reliability.CASES),
        *(f"examples/{name}" for name in reliability.EXAMPLE_OUTPUTS.values()),
        *_terminal_fixtures(),
    }
    prefix = "tests/expected/"
    stored = set()
    for name in _tracked_files():
        if name.startswith(prefix):
            parts = name.removeprefix(prefix).split("/")
            depth = 2 if parts[0] in ("synth", "examples") else 1
            stored.add("/".join(parts[:depth]))
    return [
        *(f"{prefix}{name} is used by no test" for name in sorted(stored - used)),
        *(f"{prefix}{name} is used, but not stored" for name in sorted(used - stored)),
    ]


def _documents() -> list[str]:
    """List the repository's documents that git tracks, for the spelling check."""
    result = run_text(
        ["git", "ls-files", "*.md", "docs"],
        cwd=SOURCE_ROOT,
        capture_output=True,
    )
    return sorted(
        name
        for name in result.stdout.splitlines()
        if not name.startswith(("src/", "tests/")) and (SOURCE_ROOT / name).is_file()
    )


def _ruff(*args: str) -> subprocess.CompletedProcess[str]:
    return run_text(
        [sys.executable, "-m", "ruff", *args, "src/", "tests/"],
        cwd=SOURCE_ROOT,
        capture_output=True,
    )


class _Collector:
    """Pytest plugin that keeps the collected test items."""

    def __init__(self) -> None:
        self.items = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.items = list(session.items)


@functools.cache
def _collect_suite() -> list[pytest.Item]:
    """Collect every test in the suite, whatever subset is being run."""
    collector = _Collector()
    with redirect_stdout(io.StringIO()):
        pytest.main(
            [
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                "-n",
                "0",
                "-m",
                "extended or not extended",
                str(TEST_DIR),
            ],
            plugins=[collector],
        )
    return collector.items


# Marks a walkthrough block of copy commands, run from build/walkthrough.
TRANSFER = "<transfer>"


def _walkthrough_command(line: str, where: str, root: Path) -> None:
    """Run one command from the walkthrough, as its reader would.

    Run pprl in ``where``, typing yes when pprl destroy asks.
    """
    words = shlex.split(line)
    assert words[0] == "pprl", line
    argv = words[1:]
    answers = DESTROY_YES if argv[0] == "destroy" else None
    rc, output = run_in(root / where, argv, answers)
    assert rc == 0, f"{line!r} failed:\n{output}"


def _walkthrough_transfer(line: str, root: Path) -> None:
    """Carry out one copy command from the walkthrough, in ``root``.

    The commands are ``cp`` and ``mkdir``, done here with shutil and pathlib
    rather than a shell, so the test runs on every platform. A ``cp`` names
    only the folder it copies into, ending in ``/``, so that each file keeps
    its name; the folder must exist, as ``cp`` requires. A ``*`` in a ``cp``
    source matches the files it names, which are all copied into the folder.
    No file is deleted by hand: only ``pprl destroy`` deletes a workspace's
    files.
    """
    command, *paths = shlex.split(line)
    assert all(not p.startswith("-") for p in paths), f"No options: {line!r}"
    if command == "mkdir":
        assert len(paths) == 1, f"One folder per mkdir: {line!r}"
        (root / paths[0]).mkdir()
    elif command == "cp":
        assert len(paths) == 2, f"One source and one target per cp: {line!r}"
        source, target = paths
        assert target.endswith("/"), f"A cp names only its target folder: {line!r}"
        assert (root / target).is_dir(), f"{line!r} copies into no folder"
        matches = sorted(root.glob(source)) if "*" in source else [root / source]
        assert matches, f"{line!r} matched no file"
        for match in matches:
            copied = root / target / match.name
            assert not copied.exists(), f"{line!r} overwrites {match.name}"
            shutil.copy(match, copied)
    else:
        pytest.fail(f"Only cp and mkdir in a copy block: {line!r}")


def _walkthrough_run(line: str, where: str, root: Path) -> str:
    """Run one line of a walkthrough block: a copy command, or pprl in ``where``.

    Return the kind of line, ``TRANSFER`` or ``"pprl"``.
    """
    if where == TRANSFER:
        _walkthrough_transfer(line, root)
        return TRANSFER
    _walkthrough_command(line, where, root)
    return "pprl"


def _assert_own_receipts_alone(root: Path) -> None:
    """Every folder in ``root`` is a destroyed workspace, holding its own receipt.

    The receipts the coordinator received are gone too: its own destroy
    deleted them.
    """
    owners = ["coordinator", "linkage", "merge", "site_a", "site_b"]
    assert sorted(p.name for p in root.iterdir()) == owners, (
        "Every folder is a workspace"
    )
    for workspace in root.iterdir():
        files = [f for f in workspace.rglob("*") if f.is_file()]
        receipts = list(workspace.glob(RECEIPT_GLOB))
        assert files == receipts, f"{workspace.name} holds more than its receipt"
        assert [r.name.split(".receipt_")[0] for r in receipts] == [workspace.name], (
            f"{workspace.name} holds a receipt other than its own"
        )


class TestMaintainability:
    """Checks of the code itself: lint, format, types, spelling and conventions."""

    def test_ruff_check(self) -> None:
        """The code has no lint violations."""
        result = _ruff("check")
        assert result.returncode == 0, (
            f"ruff check failed:\n{result.stdout}{result.stderr}"
        )

    def test_ruff_format(self) -> None:
        """All files are formatted consistently."""
        result = _ruff("format", "--check")
        assert result.returncode == 0, (
            f"ruff format --check failed:\n{result.stdout}{result.stderr}"
        )

    @pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
    def test_type_check(self, platform: str) -> None:
        """The code type-checks for every platform pprl runs on.

        This holds whichever platform the tests are running on.
        """
        result = run_text(
            [sys.executable, "-m", "ty", "check", "--python-platform", platform],
            cwd=SOURCE_ROOT,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"ty check failed for {platform}:\n{result.stdout}{result.stderr}"
        )

    def test_spelling(self) -> None:
        """The code, tests, documents and project files have no known misspellings."""
        result = run_text(
            [
                sys.executable,
                "-m",
                "codespell_lib",
                "--toml",
                "pyproject.toml",
                "src",
                "tests",
                "examples",
                "Makefile",
                "pyproject.toml",
                ".github",
                *_documents(),
            ],
            cwd=SOURCE_ROOT,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"codespell failed:\n{result.stdout}{result.stderr}"
        )

    def test_text_files_tidy(self) -> None:
        r"""The tracked text files that ruff does not format are tidy.

        Each is UTF-8 with no byte order mark, has \n line endings, no
        trailing whitespace and no tabs (but in the Makefile's recipes), and
        ends with one newline. Python, CSV data, stored outputs and the OMOP
        CDM files are left out (see _WHITESPACE_SKIPPED).
        """
        problems = text_layout_problems()
        assert not problems, "Untidy text files:\n" + "\n".join(
            f"  {problem}" for problem in problems
        )

    def test_actions_pinned(self) -> None:
        """Every GitHub Action the workflows use is pinned to one commit.

        Each names a full commit SHA and, in a comment, the version that commit
        is, so that a moved or hijacked tag cannot change what CI runs.
        """
        problems = unpinned_actions()
        assert not problems, "Actions not pinned to a commit:\n" + "\n".join(
            f"  {problem}" for problem in problems
        )

    def test_makefile_targets_listed(self) -> None:
        """Every make target is declared phony and shown by ``make help``.

        Neither .PHONY nor the help text names a target that does not exist.
        """
        problems = makefile_problems()
        assert not problems, "The Makefile's targets are not all listed:\n" + (
            "\n".join(f"  {problem}" for problem in problems)
        )

    def test_fixtures_all_used(self) -> None:
        """Every stored output in tests/expected is one a test compares with.

        So a stored output is not left behind when its test is renamed or
        removed (make update-fixtures rewrites outputs, but deletes only the
        files inside a folder it rewrites). Equally, every output a test
        compares with is stored and tracked by git.
        """
        problems = fixture_problems()
        assert not problems, "Stored outputs out of step with the tests:\n" + (
            "\n".join(f"  {problem}" for problem in problems)
        )

    def test_every_test_has_one_objective(self) -> None:
        """Every test belongs to exactly one objective."""
        items = _collect_suite()
        assert len(items) > len(OBJECTIVES), "The test suite was not collected"
        wrong = {}
        for item in items:
            objectives = {m.name for m in item.iter_markers() if m.name in OBJECTIVES}
            if len(objectives) != 1:
                wrong[item.nodeid] = sorted(objectives)
        assert not wrong, (
            f"Each test needs exactly one of the markers {sorted(OBJECTIVES)}:\n"
            + "\n".join(f"  {node}: {found or 'none'}" for node, found in wrong.items())
        )

    def test_every_test_has_a_description(self) -> None:
        """Every test says what it checks in its docstring."""
        missing = [
            item.nodeid
            for item in _collect_suite()
            if not getattr(getattr(item, "function", None), "__doc__", None)
        ]
        assert not missing, "Tests without a docstring:\n" + "\n".join(
            f"  {node}" for node in missing
        )

    def test_usability_grid_complete(self) -> None:
        """Every kind of input is covered, or excused, for every command.

        Every command pprl has is a column of the grid, or is named in
        OUTSIDE_GRID with the test file that covers its inputs.
        """
        problems = grid_problems(CASES, cli._COMMANDS)
        assert not problems, "The usability grid is incomplete:\n" + "\n".join(
            f"  {problem}" for problem in problems
        )

    def test_messages_only_in_catalog(self) -> None:
        """Every message a user can see is written in the message catalog.

        This way all of them can be reviewed in one place.
        """
        found = message_catalog.literal_messages()
        assert not found, (
            "Text shown to users outside src/pprl/_messages.py:\n"
            + "\n".join(f"  {line}" for line in found)
        )

    def test_message_catalog_rules(self) -> None:
        """Every message in the catalog says something of its own, and is used."""
        problems = message_catalog.catalog_problems()
        assert not problems, "The message catalog breaks its rules:\n" + "\n".join(
            f"  {problem}" for problem in problems
        )

    def test_output_only_through_ui(self) -> None:
        """Pprl's own code shows output only through the UI module.

        This way everything shown is logged. There is no print, no writing to
        sys.stdout or sys.stderr, and no logging, except in the places listed
        in OUTPUT_ALLOWED.
        """
        problems = output_bypassing_ui()
        assert not problems, "Output that bypasses _ui.py (and the log):\n" + (
            "\n".join(f"  {problem}" for problem in problems)
        )

    def test_text_through_helpers(self) -> None:
        """Every text file pprl and the tests read or write is UTF-8.

        This holds whatever the computer's own encoding is (cp1252 on
        Windows). All text goes through the helpers listed in _TEXT_HELPERS,
        and a command's output goes through run_text. The make targets run
        only Python code that the check can read.
        """
        problems = text_io_outside_helpers()
        assert not problems, "Text read or written outside the helpers:\n" + (
            "\n".join(f"  {problem}" for problem in problems)
        )

    def test_wheel_check_without_dev_packages(self) -> None:
        """The wheel check imports none of the development packages.

        CI runs it without them, including without pytest.
        """
        result = run_text(
            [
                sys.executable,
                "-c",
                "import sys; sys.modules['pytest'] = None; import tests.wheel_check",
            ],
            cwd=SOURCE_ROOT,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr

    def test_messages_doc_current(self) -> None:
        """The messages document lists every message in the catalog."""
        assert_text_matches(
            message_catalog.render_markdown(), message_catalog.DOC_PATH, "messages doc"
        )

    def test_walkthrough_runs(self, tmp_path: Path) -> None:
        """Every step of the walkthrough works as written.

        This covers everything from the coordinator's first command to the
        last workspace being destroyed.
        """
        root = tmp_path / "walkthrough"  # The walkthrough's build/walkthrough folder
        root.mkdir()
        lines = read_text(SOURCE_ROOT / "docs" / "walkthrough.md").splitlines()
        where, to_create, block = None, None, None
        n_run = {TRANSFER: 0, "pprl": 0}
        for line in lines:
            if block is not None:
                if line.startswith("```"):
                    if to_create:
                        write_text(root / to_create, "\n".join(block) + "\n")
                        to_create = None
                    block, where = None, None
                elif where is not None and line.strip():
                    n_run[_walkthrough_run(line, where, root)] += 1
                else:
                    block.append(line)
                continue
            if line.startswith("```"):
                block = []
            elif created := re.search(r"Create `([^`]+)`:", line):
                to_create = created.group(1)
            elif "From `build/walkthrough`, run:" in line:
                where = TRANSFER
            elif step := re.search(r"In `([^`]+)`, run:", line):
                where = step.group(1)
        assert n_run["pprl"] >= 26, "The walkthrough's pprl commands were not found"
        assert n_run[TRANSFER] >= 28, "The walkthrough's copy commands were not found"
        _assert_own_receipts_alone(root)


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_pins_match_the_lock() -> None:
    """The wheel pins every package to exactly the version uv.lock has (make pins)."""
    locked = pins.locked()
    assert pins.pinned() == locked, (
        "Run `make pins` (or `uv run --frozen python -m tests.pins`)"
    )
    names = {re.split(r"[=<>!~ ;\[]", pin, maxsplit=1)[0].lower() for pin in locked}
    for requirement in pins.requirements():
        name = re.split(r"[=<>!~ ;\[]", requirement, maxsplit=1)[0].lower()
        assert name in names, (
            f"{requirement} is in [tool.pprl] requires, but not pinned"
        )


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_lock_current() -> None:
    """uv.lock is up to date with pyproject.toml, checked without network access."""
    result = run_text(
        ["uv", "lock", "--check", "--offline"],
        cwd=SOURCE_ROOT,
        capture_output=True,
    )
    assert result.returncode == 0, (
        "Run `make pins` (or `uv run --frozen python -m tests.pins`):\n"
        f"{result.stdout}{result.stderr}"
    )
