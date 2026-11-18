"""Provide shared constants and helpers for the test suite.

Every test belongs to exactly one objective, given by a pytest marker
(``@pytest.mark.security``, or ``pytestmark = pytest.mark.security`` for a
whole module). The pytest configuration registers the OBJECTIVES below as
markers, and a maintainability test checks that every test has one.

Most tests call the internal functions directly, with a pytest tmp_path as
their data and output folder. Usability cases run through the CLI instead,
in a workspace that ``cli_in`` makes in a tmp_path.
"""

import contextlib
import csv
import dataclasses
import io
import os
import re
import sys
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import pandas as pd
import pytest
import yaml

from pprl import _bundle, _hash, _locking, _pprl_file, _wording
from pprl import _messages as msg
from pprl._io import (
    for_writing,
    read_csv_frame,
    read_text,
    write_csv_frame,
    write_text,
)
from pprl._pprl_file import file_name
from pprl._shared import STUDY_ID, Member
from pprl._ui import ANSI, logged_runs
from pprl._workspace import MARKER_NAME, WorkspaceError, is_workspace
from pprl._workspace import _write as write_marker
from pprl._workspace import create as create_workspace
from pprl._workspace import read as _read_marker
from pprl._workspace import read as read_workspace
from pprl.cli import main
from pprl.synth import synthesize

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

    from pprl._pprl_file import PprlFile
    from pprl._wording import Message
    from pprl._workspace import Marker

OBJECTIVES = {
    "security": "Hashes and messages reveal neither patient identifiers nor "
    "the secret, and file access stays inside the workspace",
    "reliability": "Commands do what they are configured to do, give the same "
    "result every time, and each output is a valid input to the next",
    "quality": "Linkage finds the true matches under realistic data quality, "
    "and behaves predictably as the threshold changes",
    "usability": "Every kind of user input is accepted, or rejected with a "
    "clear message",
    "maintainability": "The code passes lint and formatting checks, every "
    "message is in the catalog, and the test suite follows its own conventions",
    "performance": "How long each command takes, and how much memory it needs, "
    "by the number of records, against a baseline (make bench)",
}

TEST_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = TEST_DIR.parent
# The folder with the example project.yml, deployment.yml and scenarios.
EXAMPLES = SOURCE_ROOT / "examples"
EXAMPLES_DIR = EXAMPLES / "scenarios"
CONFIG_DIR = TEST_DIR / "configs"
EXPECTED_DIR = TEST_DIR / "expected"

# Where pprl destroy leaves a workspace's receipt, as a glob.
RECEIPT_GLOB = "outbox/coordinator/*.receipt_*.txt"

# Set UPDATE_FIXTURES=1 to rewrite stored output instead of comparing with it.
UPDATE_FIXTURES = os.environ.get("UPDATE_FIXTURES", "").lower() in ("1", "true", "yes")

# The id column and fields of the patient files synth writes, and example
# hashing settings. (A real project's settings come from the coordinator.)
# The weights and fill ratio give the fields 500, 200, 200, 200 and 300 bits.
SYNTH_ID = "person_id"
SYNTH_FIELDS = {
    "name": 500,
    "city": 200,
    "state": {"weight": 200, "type": "state"},
    "zip": {"weight": 200, "type": "zip"},
    "dob": {"weight": 300, "type": "date"},
}
SYNTH_SECRET = "0123456789abcdef" * 4  # 64 hex characters, like a real secret


class HashSizes(TypedDict):
    """A project's hash length and fill ratio, as keyword arguments."""

    hash_length: int
    fill_ratio: float


# The test project's hash length and fill ratio. A real project chooses its own.
HASH_SIZES: HashSizes = {"hash_length": 1024, "fill_ratio": 1.0}
# A merge agent's rules for the scores table that each site has in the
# usability grid's inputs.
MERGE_TABLES: dict[str, dict] = {
    "scores": {
        "primary_key": ["person_id"],
        "column": {
            "score": {"rank_by": [{"take_highest": {"column": "score"}}]},
        },
    }
}

# The same table as a site's bundle gives it: its columns and primary key,
# with no rules, so that pprl select keeps every row.
SITE_TABLES = {
    "scores": {
        "columns": ["person_id", "score"],
        "primary_key": ["person_id"],
        "keep": [],
        "censor": [],
    }
}


# The sites of a test project, and its linkage agent's threshold.
SITES = ("site_a", "site_b")
THRESHOLD = 0.95

# A test project's project.yml, as the coordinator writes it for pprl bundles
# (see ``coordinator_workspace``).
PROJECT_SETTINGS = {
    "name": "Test project",
    "id": SYNTH_ID,
    "sites": list(SITES),
    "fields": SYNTH_FIELDS,
    **HASH_SIZES,
    "threshold": THRESHOLD,
    "secret_check": True,
    "tables": MERGE_TABLES,
}


# ---------------------------------------------------------------------------
# Running commands
# ---------------------------------------------------------------------------


def call_kwargs(tmp_path: Path, *, output_folder: Path | None = None) -> dict:
    """Return the data folder and output folder arguments for an internal function."""
    return {
        "data_folder": tmp_path,
        "output_folder": tmp_path if output_folder is None else output_folder,
    }


# The project that every test workspace belongs to, and its bundle set.
TEST_PROJECT = "00000000-0000-4000-8000-000000000000"
TEST_SET = "00000000-0000-4000-8000-000000000001"


def provenance(site: str | None = None) -> dict:
    """Return the project, bundle set and site of a test's pprl files.

    They have the form the commands take.
    """
    return {"project": TEST_PROJECT, "set": TEST_SET, "site": site}


def write_bundle(folder: Path, **settings: object) -> dict:
    """Write the bundle for the workspace ``folder``, as pprl bundles would.

    The bundle holds the role's test settings, with ``settings`` replacing
    any of them. Return its contents.
    """
    marker = read_workspace(folder)
    defaults = {
        "site": {
            "site": marker.site,
            "id": SYNTH_ID,
            "fields": SYNTH_FIELDS,
            **HASH_SIZES,
            "xor_folds": 0,
            "project_salt": None,
            "record_salt": None,
            "tables": SITE_TABLES,
        },
        "linkage": {
            "sites": list(SITES),
            "threshold": THRESHOLD,
            "secret_check": True,
        },
        "merge": {
            "id": SYNTH_ID,
            "sites": list(SITES),
            "tables": MERGE_TABLES,
        },
    }[marker.role]
    contents = {
        "project": {"id": marker.project, "name": PROJECT_SETTINGS["name"]},
        "set": TEST_SET,
        "release": version("pprl"),
        "role": marker.role,
        **defaults,
        **settings,
    }
    assert marker.bundle_name is not None  # Every role here has a bundle.
    path = Path(folder) / marker.bundle_name
    path.unlink(missing_ok=True)
    _bundle.write(path, contents)
    marker = dataclasses.replace(marker, bundle_set=contents["set"])
    with for_writing(Path(folder) / MARKER_NAME, replace=True) as handle:
        write_marker(handle, marker)  # as pprl init records it
    return contents


def write_site_keys(folder: Path, secret: str = SYNTH_SECRET) -> None:
    """Give a site's workspace the files that pprl init and pprl secrets give it.

    These are, in ``folder``: the private key, the public key in
    outbox/coordinator/,
    and the secret file, holding ``secret``.
    """
    marker = read_workspace(folder)
    site = marker.site
    of_site = {"project": marker.project, "set": marker.bundle_set, "site": site}
    public = Path(folder) / "outbox" / "coordinator" / file_name("public_key", site)
    _locking.write_key_pair(
        Path(folder) / file_name("private_key", site), public, of_site
    )
    _pprl_file.write(
        Path(folder) / file_name("secret", site),
        "secret",
        {Member.KEY: _locking.lock(secret, _pprl_file.read(public))},
        provenance=of_site,
    )


def make_workspace(
    folder: Path, role: str = "site", site: str | None = "site_a", **settings: object
) -> Marker:
    """Make ``folder`` a workspace, as pprl init does, and return the marker.

    Create the folder, write its marker, and put the role's bundle there (see
    ``write_bundle``). For a site, also add its keys and secret file (see
    ``write_site_keys``).
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if role != "site":
        site = None
    create_workspace(
        folder,
        project=TEST_PROJECT,
        role=role,
        site=site,
        bundle_set=None if role == "coordinator" else "(written below)",
    )
    if role != "coordinator":
        write_bundle(folder, **settings)
    if role == "site":
        write_site_keys(folder)
    return read_workspace(folder)


@contextlib.contextmanager
def cli_in(directory: Path | str) -> Generator[None]:
    """Run the block with ``directory`` as the CLI's workspace.

    The directory becomes the current folder, and is made a workspace first
    if it is not one already.
    """
    directory = Path(directory)
    if not is_workspace(directory):
        make_workspace(directory)
    with contextlib.chdir(directory):
        yield


def python_files(*folders: str) -> list[Path]:
    """List every Python module under the repository's ``folders``, in order."""
    return sorted(p for folder in folders for p in (SOURCE_ROOT / folder).rglob("*.py"))


def seeds(count: int, quick: int) -> list:
    """Return seeds 0 to ``count`` for a test of random cases.

    The first ``quick`` seeds run with the normal suite. The rest are marked
    extended, so they run only with every test (make test-all, or
    pytest -m "extended or not extended").
    """
    return [
        seed if seed < quick else pytest.param(seed, marks=pytest.mark.extended)
        for seed in range(count)
    ]


def coordinator_workspace(folder: Path, **settings: object) -> Marker:
    """Make the coordinator's workspace at ``folder``, with every bundle.

    The workspace is as it is after pprl bundles has written every bundle
    into the outbox, from a project.yml with the test project's settings.
    ``settings`` replaces any of those settings (see ``PROJECT_SETTINGS``).
    Return the marker.
    """
    make_workspace(folder, role="coordinator")
    write_yaml(Path(folder) / "project.yml", PROJECT_SETTINGS | settings)
    rc, output = run_in(folder, ["bundles"])
    assert rc == 0, output
    return read_workspace(folder)


@contextlib.contextmanager
def _typing(answers: str | None) -> Generator[None]:
    """Run the block with ``answers`` as what the user types, if given."""
    if answers is None:
        yield
        return
    typed, sys.stdin = sys.stdin, io.StringIO(answers)
    try:
        yield
    finally:
        sys.stdin = typed


def run_in(
    folder: Path, argv: list[str], answers: str | None = None
) -> tuple[int, str]:
    """Run pprl with ``argv`` in ``folder``, as a user would there.

    Type ``answers`` if pprl asks. Return (exit code, everything shown,
    without color codes).
    """
    out = io.StringIO()
    with (
        contextlib.chdir(folder),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(out),
        _typing(answers),
    ):
        rc = main(argv)
    return rc, strip_ansi(out.getvalue())


def files_in(folder: Path) -> list[str]:
    """List every file and folder under ``folder``, relative to it.

    Names use / on every platform, as pprl shows them.
    """
    folder = Path(folder)
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


class Logged(str):  # noqa: SLOT000 (it has attributes)
    """A log section's body, which compares equal to the output it logged.

    The text is the same, except that each <withheld> in the log stands for
    whatever the user supplied on that line of the output.
    """

    def _pattern(self) -> re.Pattern[str]:
        escaped = re.escape(str(self)).replace(re.escape(_wording.WITHHELD), "[^\\n]*?")
        return re.compile(escaped)

    def __eq__(self, other: object) -> bool:
        """Tell whether the output ``other`` is what this log section logged."""
        if not isinstance(other, str):
            return NotImplemented
        return self._pattern().fullmatch(other) is not None

    def __ne__(self, other: object) -> bool:
        """Tell whether the output ``other`` is not what this section logged."""
        equal = self.__eq__(other)
        return equal if equal is NotImplemented else not equal

    __hash__ = str.__hash__


def log_of(folder: Path) -> Path:
    """Return the path of the log of the workspace ``folder``, such as site_a.log."""
    return Path(folder) / _read_marker(Path(folder)).log_name


def has_log(folder: Path) -> bool:
    """Tell whether ``folder`` holds any log file."""
    return any(Path(folder).glob("*.log"))


def _sections(folder: Path) -> list[list]:
    """Read the runs logged in the log of the workspace ``folder``.

    Each run is [header, system line, body lines, footer, [INFO] lines].
    """
    try:
        path = log_of(folder)
    except WorkspaceError:
        return []
    if not path.exists():
        return []
    sections = []
    tag = msg.INFO_TAG + " "
    for run in logged_runs(read_text(path)):
        assert set(run.after) <= {""}, f"After a footer, a blank line: {run.after!r}"
        body = [line for line in run.lines if not line.startswith(tag)]
        info_lines = [line[len(tag) :] for line in run.lines if line.startswith(tag)]
        sections.append([run.header, run.system, body, run.footer, info_lines])
    return sections


def log_sections(folder: Path) -> list[tuple]:
    """Read the runs logged in the log of the workspace ``folder``, oldest first.

    Each run is (header, system line, body, footer). The body is the run's
    output as shown, one line after another, without the [INFO] lines
    (see ``log_info_lines``). It is a Logged string, in which <withheld>
    matches what the user supplied. A run that was killed has no footer
    (None).
    """
    return [
        (header, system, Logged("".join(f"{line}\n" for line in body)), footer)
        for header, system, body, footer, _ in _sections(folder)
    ]


def log_info_lines(folder: Path) -> list[list[str]]:
    """Return each logged run's [INFO] lines, oldest first, without their tag."""
    return [info_lines for *_, info_lines in _sections(folder)]


def write_yaml(path: Path, contents: object, *, replace: bool = False) -> None:
    """Write ``contents`` to ``path`` as YAML, as a person would.

    Keys stay in their given order, as a person would write a config file.
    An existing file is never overwritten, unless ``replace`` is true.
    """
    write_text(path, yaml.safe_dump(contents, sort_keys=False), replace=replace)


def run_scenario(folder: Path, scenario: dict, name: str = "scenario") -> int:
    """Write a scenario dict to <folder>/<name>.yml and run synth on it.

    The dataset is written to <folder>/<name>/. Returns the exit code.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    write_yaml(folder / f"{name}.yml", scenario)
    return synthesize(scenario=f"{name}.yml", data_folder=folder, output_folder=folder)


def synthesize_patients(
    tmp_path: Path,
    extra_columns: dict | None = None,
    *,
    n: int = 100,
    site: str = "site_a",
    seed: int | str = 42,
    output: str | None = None,
    pii: dict | None = None,
    cleaned: bool = True,
) -> int:
    """Write one site's synthetic patient file to tmp_path/output.

    Run a one-site scenario with n generated individuals. By default, the
    file is a cleaned patient file (site_a.clean.pprl), as pprl clean writes
    it (see ``write_cleaned``). With ``cleaned=False``, it is the CSV that
    synth writes, ready for clean (patients.csv by default). Return the exit
    code.
    """
    if output is None:
        output = file_name("clean", site) if cleaned else "patients.csv"
    scenario = {
        "seed": seed,
        "sites": [site],
        "outputs": ["patients"],
        "generated_individuals": [{"sites": [site], "count": n}],
    }
    if pii:
        scenario["pii"] = pii
    name = f"_synth_{Path(output).name.split('.')[0]}"
    rc = run_scenario(tmp_path, scenario, name)
    if rc != 0:
        return rc
    df = read_csv_frame(tmp_path / name / "patients.csv")
    for col_name, values in (extra_columns or {}).items():
        df[col_name] = [values[i % len(values)] for i in range(len(df))]
    if cleaned:
        write_cleaned(df, tmp_path / output, site)
    else:
        write_csv_frame(df, tmp_path / output)
    return 0


def as_cleaned(df: pd.DataFrame) -> pd.DataFrame:
    """Return a synthetic patient file's rows as pprl clean writes them.

    This is for tests that hash the rows directly. The person_ids become the
    study_ids. clean would draw study_ids at random, but keeping synth's
    person_ids keeps the ground truth, because the same individual has the
    same person_id at every site (``person_ids: shared``).
    """
    return df.rename(columns={SYNTH_ID: STUDY_ID})


def write_cleaned(
    df: pd.DataFrame, path: Path, site: str, selected: PprlFile | None = None
) -> dict:
    """Write the synthetic patient file ``df`` as ``site``'s cleaned file at ``path``.

    The file is written as pprl clean would write it, except that the
    person_ids become the study_ids (see ``as_cleaned``). ``df`` has the form
    synth writes. If ``selected`` (a PprlFile) is given, the file records it
    as its input.
    """
    study_ids = df[[SYNTH_ID]].assign(**{STUDY_ID: df[SYNTH_ID]})
    return _pprl_file.write(
        path,
        "clean",
        {"patients": as_cleaned(df), "study_ids": study_ids},
        provenance=provenance(site),
        inputs=[selected] if selected else [],
    )


def write_selected(
    path: Path, site: str, tables: dict[str, pd.DataFrame], id_column: str = SYNTH_ID
) -> PprlFile:
    """Write ``tables`` as the selected tables file of ``site`` at ``path``.

    ``tables`` maps names to DataFrames keyed by the site's ids. They are
    written as pprl select writes the rows it kept. Return the file, as read.
    """
    ids = sorted({i for df in tables.values() for i in df[id_column] if i != ""})
    _pprl_file.write(
        path,
        "selected",
        {Member.COHORT: pd.DataFrame({id_column: ids}, dtype=str), **tables},
        provenance=provenance(site),
    )
    return _pprl_file.read(path)


def rewrite_header(path: Path, **changes: object) -> None:
    """Rewrite the pprl file at ``path`` with ``changes`` to its header.

    The members stay as they were. Only a deliberate edit could do this.
    Tests use it for a file from another release, project, set or site.
    """
    original = _pprl_file.read(path, check_release=False)
    header = original.header | changes
    write_again(path, original.members, header)


def write_again(path: Path, members: dict, header: dict) -> None:
    """Write ``members`` as the pprl file at ``path``, using ``header``'s keys.

    Any existing file is replaced. The header's kind, provenance, inputs and
    any other keys it gives (such as a release or an id) replace those that
    pprl would give a new file.
    """
    header = dict(header)
    kind = header.pop("kind")
    inputs = header.pop("inputs", [])
    header.pop("members", None)  # Recomputed as the members are written
    Path(path).unlink(missing_ok=True)
    _pprl_file.write(path, kind, members, provenance=header, inputs=inputs, **header)


def rewrite_marker(folder: Path, **changes: object) -> None:
    """Change values in a workspace's marker, as an editor would."""
    path = Path(folder) / MARKER_NAME
    contents = yaml.safe_load(read_text(path)) | changes
    write_yaml(path, contents, replace=True)


def members(path: Path, name: str) -> pd.DataFrame:
    """Read the member ``name`` of the pprl file at ``path`` as a DataFrame."""
    return _pprl_file.read(path).members[name]


def two_site_scenario(
    shared: int,
    only_a: int = 0,
    only_b: int = 0,
    seed: int | str = 2026,
    pii: dict | None = None,
) -> dict:
    """Make a scenario with ``shared`` individuals at both site_a and site_b.

    ``only_a`` and ``only_b`` add individuals with a record at only one site.
    """
    groups = [{"sites": ["site_a", "site_b"], "count": shared}]
    if only_a:
        groups.append({"sites": ["site_a"], "count": only_a})
    if only_b:
        groups.append({"sites": ["site_b"], "count": only_b})
    scenario = {
        "seed": seed,
        "sites": ["site_a", "site_b"],
        "outputs": ["patients"],
        "generated_individuals": groups,
    }
    if pii:
        scenario["pii"] = pii
    return scenario


def synthesize_sites(
    tmp_path: Path,
    scenario: dict,
    sites: Sequence[str] = ("site_a", "site_b"),
    name: str = "sites",
) -> None:
    """Run a multi-site scenario, and write each site's cleaned patient file.

    Each site in ``sites`` gets its cleaned patient file in tmp_path
    (site_a.clean.pprl and so on; see ``write_cleaned``). Matching records
    share a study_id.
    """
    rc = run_scenario(tmp_path, scenario, name)
    assert rc == 0, f"synth failed with exit code {rc}"
    for site in sites:
        df = read_csv_frame(tmp_path / name / site / "patients.csv")
        write_cleaned(df, tmp_path / file_name("clean", site), site)


def hash_both_sites(
    tmp_path: Path, fields: dict | None = None, secret: str | None = None
) -> list[int]:
    """Hash site_a's and site_b's cleaned files, and return the two exit codes.

    The inputs are site_a.clean.pprl and site_b.clean.pprl, and the outputs
    are site_a.hashes.pprl and site_b.hashes.pprl.
    """
    results = []
    for site in ("site_a", "site_b"):
        rc = _hash.hash_records(
            patients=file_name("clean", site),
            secret=secret if secret is not None else SYNTH_SECRET,
            fields=fields if fields is not None else SYNTH_FIELDS,
            output=file_name("hashes", site),
            **HASH_SIZES,
            provenance=provenance(site),
            **call_kwargs(tmp_path),
        )
        results.append(rc)
    return results


# ---------------------------------------------------------------------------
# Comparing with stored output
# ---------------------------------------------------------------------------

_TIMER_RE = re.compile(r"\(\d{2}:\d{2}:\d{2}\)")
_RELEASE_DATE_COLUMNS = ("source_release_date", "cdm_release_date")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from terminal output."""
    return ANSI.sub("", text)


def shows(message: Message | str, output: str) -> bool:
    """Tell whether ``output`` shows ``message``, however its lines are laid out.

    ``message`` is a Message, with any details, or plain text.
    """
    output = " ".join(strip_ansi(output).split())
    if not isinstance(message, _wording.Message):
        return " ".join(message.split()) in output
    # A problem with the settings shows the coordinator's fix in the commands
    # on the project, and the user's fix in the others (see Message).
    for fix in dict.fromkeys((message.user_fix, message.fix)):
        template = message.text + (" " + _wording.FIX_PREFIX + fix if fix else "")
        if re.search(_wording.template_pattern(" ".join(template.split())), output):
            return True
    return False


def markdown_cell(text: str) -> str:
    """Format ``text`` as one Markdown table cell: on one line, with bars escaped."""
    return text.replace("|", "\\|").replace("\n", " ")


def markdown_page(title: str, source: str, generator: str, lines: list[str]) -> str:
    """Make a generated Markdown page.

    The page has ``title``, a note that ``generator`` made it from ``source``
    and that it should not be edited, and then ``lines``.
    """
    note = f"<!-- Generated from {source} by {generator}. Do not edit by hand. -->"
    return "\n".join([f"# {title}", "", note, "", *lines]) + "\n"


def _normalize_terminal(text: str) -> str:
    """Strip ANSI codes and normalize content that changes between runs."""
    text = strip_ansi(text)
    text = _TIMER_RE.sub("(ELAPSED)", text)
    lines = text.splitlines()
    lines = [line.rsplit("\r", 1)[-1] for line in lines]
    lines = [line for line in lines if line.strip()]
    return "\n".join(lines) + "\n" if lines else ""


def assert_text_matches(actual: str, expected_path: Path, what: str) -> None:
    """Compare text with a stored file, or rewrite it under UPDATE_FIXTURES."""
    if UPDATE_FIXTURES:
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        write_text(expected_path, actual, replace=True)
        return
    if not expected_path.exists():
        pytest.fail(
            f"Expected {what} file does not exist: {expected_path}\n"
            f"Run with UPDATE_FIXTURES=1 to create it."
        )
    assert actual == read_text(expected_path), (
        f"{what.capitalize()} differs from {expected_path}.\n"
        f"Run with UPDATE_FIXTURES=1 to update."
    )


def assert_terminal_output_matches(captured_output: str, name: str) -> None:
    """Assert that terminal output matches the stored expected/<name>.txt.

    ANSI codes and the time on a [DONE] line are normalized before comparing.
    """
    assert_text_matches(
        _normalize_terminal(captured_output),
        EXPECTED_DIR / f"{name}.txt",
        "terminal output",
    )


def assert_output_matches(output_path: Path | str, expected_path: Path) -> None:
    """Assert that an output file matches its stored copy.

    For a CSV file, compare its text. For a pprl file, compare its one
    member as CSV, because its header has a new id and time on each run.
    """
    path = Path(output_path)
    if path.suffix == ".pprl":
        [member] = _pprl_file.read(path).members.values()
        text = member.to_csv(index=False, lineterminator="\n")
    else:
        text = read_text(path)
    assert_text_matches(text, expected_path, "output")


def _normalized_fixture_text(path: Path) -> str:
    """Return a file's text with cdm_source release dates (always today) replaced."""
    text = read_text(Path(path))
    if Path(path).name != "cdm_source.csv":
        return text
    rows = list(csv.reader(io.StringIO(text)))
    positions = [rows[0].index(column) for column in _RELEASE_DATE_COLUMNS]
    for row in rows[1:]:
        for i in positions:
            row[i] = "(release date)"
    normalized = io.StringIO()
    csv.writer(normalized, lineterminator="\n").writerows(rows)
    return normalized.getvalue()


def assert_folder_matches(actual_dir: Path | str, expected_dir: Path | str) -> None:
    """Assert that a dataset folder matches its stored copy, file by file.

    The release dates in cdm_source.csv are today's date, so they are
    normalized before comparing. With UPDATE_FIXTURES set, the stored copy
    is replaced by the actual folder.
    """
    actual_dir, expected_dir = Path(actual_dir), Path(expected_dir)
    actual = {p.relative_to(actual_dir) for p in actual_dir.rglob("*") if p.is_file()}

    if UPDATE_FIXTURES:
        if expected_dir.exists():
            for old in sorted(expected_dir.rglob("*"), reverse=True):
                old.unlink() if old.is_file() else old.rmdir()
        for relative in sorted(actual):
            target = expected_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            write_text(
                target, _normalized_fixture_text(actual_dir / relative), replace=True
            )
        return

    if not expected_dir.exists():
        pytest.fail(
            f"Expected folder does not exist: {expected_dir}\n"
            f"Run with UPDATE_FIXTURES=1 to create it."
        )
    expected = {
        p.relative_to(expected_dir) for p in expected_dir.rglob("*") if p.is_file()
    }
    assert actual == expected, (
        f"Files differ from {expected_dir}: "
        f"missing {sorted(map(str, expected - actual))}, "
        f"unexpected {sorted(map(str, actual - expected))}"
    )
    for relative in sorted(actual):
        assert _normalized_fixture_text(actual_dir / relative) == read_text(
            expected_dir / relative
        ), (
            f"{relative} differs from {expected_dir / relative}. "
            f"Run with UPDATE_FIXTURES=1 to update."
        )
