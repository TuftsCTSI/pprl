"""Define the usability grid: every kind of user input, by command.

Rows are kinds of input (INPUTS), and columns are commands (COMMANDS). Each
cell is one of:

  * covered: at least one Case in the usability cases module runs the input
    through the CLI, and checks the exit code and what the user sees
  * not applicable: the command has no such input. This follows from what
    the command reads and writes (Command.takes), or is stated with a
    reason in NOT_APPLICABLE
  * outside the grid: an input that several commands read through the same
    code has its cases on only one or two of them, named in its ``needs``
  * a known gap: the input applies but is not tested yet, with a reason in
    GAPS

A maintainability test checks that every cell is exactly one of these. A
Case records what pprl actually does today. When that is not what pprl
should do, the Case says so in ``problem``. ``make docs`` (or ``python -m
tests.usability_cases``) writes the grid and every case into a table for
review (see ``render_markdown``), and ``make try`` (or ``python -m
tests.playground try``) runs a case by hand in the playground.
"""

import contextlib
import io
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast, overload

from clkhash.serialization import deserialize_bitarray, serialize_bitarray

from pprl import _hash, _link, _pprl_file
from pprl._config import read_config
from pprl._guides import DEPLOYMENT_FILE
from pprl._io import read_csv_frame, write_csv_frame, write_text
from pprl._pprl_file import file_name
from pprl._wording import Message
from pprl._workspace import read as read_workspace
from pprl.cli import main
from tests.helpers import (
    EXAMPLES,
    HASH_SIZES,
    PROJECT_SETTINGS,
    SOURCE_ROOT,
    SYNTH_FIELDS,
    SYNTH_ID,
    SYNTH_SECRET,
    as_cleaned,
    call_kwargs,
    cli_in,
    make_workspace,
    markdown_cell,
    markdown_page,
    provenance,
    run_scenario,
    strip_ansi,
    two_site_scenario,
    write_again,
    write_bundle,
    write_cleaned,
    write_selected,
    write_yaml,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from typing import Any

    import pandas as pd

DOC_PATH = SOURCE_ROOT / "build" / "input-handling.md"


# ---------------------------------------------------------------------------
# The columns: commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """A command in the grid: its column, and how its cases run."""

    name: str
    summary: str
    # What the command takes from the user:
    # - "cli": only the program itself
    # - "args": command-line arguments
    # - "config": a YAML file
    # - "paths": file names
    # - "files": data files of any kind
    # - "csv": CSV files a person saved
    # - "pprl-files": pprl files, which pprl wrote
    # - "contents": a data file's columns and values, which the command checks
    # - "output": files the command writes
    # - "bundle": its settings, from the workspace's bundle
    takes: frozenset


def _command(name: str, summary: str, *takes: str) -> Command:
    return Command(name, summary, frozenset(takes))


COMMANDS = {
    c.name: c
    for c in (
        _command("pprl", "The program itself, with or without a command", "cli"),
        _command(
            "synth",
            "Synthetic data from a scenario (the coordinator)",
            "config",
            "paths",
            "output",
        ),
        _command(
            "bundles",
            "Every role's bundle, from project.yml (the coordinator)",
            "config",
            "output",
        ),
        _command(
            "guides",
            "Every role's guide, from deployment.yml (the coordinator)",
            "config",
            "output",
        ),
        _command(
            "select",
            "Select the study's rows of the site's tables (a site)",
            "bundle",
            "files",
            "csv",
            "contents",
            "output",
        ),
        _command(
            "clean",
            "Prepare patients.csv for hashing (a site)",
            "bundle",
            "files",
            "csv",
            "pprl-files",
            "contents",
            "output",
        ),
        _command(
            "hash",
            "Hash the cleaned records (a site)",
            "bundle",
            "files",
            "pprl-files",
            "contents",
            "output",
        ),
        _command(
            "pack",
            "Prepare the site's selected tables for merge (a site)",
            "bundle",
            "files",
            "pprl-files",
            "contents",
            "output",
        ),
        _command(
            "link",
            "Link the sites' hash files (the linkage agent)",
            "bundle",
            "files",
            "pprl-files",
            "output",
        ),
        _command(
            "merge",
            "Merge linked records (the merge agent)",
            "bundle",
            "files",
            "pprl-files",
            "contents",
            "output",
        ),
        _command(
            "sweep",
            "Linkage at several thresholds (the coordinator)",
            "config",
            "paths",
            "files",
            "csv",
            "pprl-files",
            "contents",
            "output",
        ),
        _command(
            "inspect",
            "Describe a file (every role)",
            "bundle",
            "args",
            "paths",
            "files",
            "csv",
            "pprl-files",
        ),
    )
}

# The commands with no column in the grid, and where their inputs are tested
# instead. Each runs on workspace files that pprl itself wrote, so its tests
# build those files directly. Every command pprl has is either a column or here.
OUTSIDE_GRID = {
    "init": "test_init.py: makes the workspace the grid's cases run in",
    "destroy": "test_destroy.py: deletes the workspace, after a prompt",
    "secrets": "test_locking.py: reads the public keys the sites' init wrote",
    "review": "test_strength.py: reads the linkage report that link wrote",
}

_TAKES = {
    "cli": "is not run as a command",
    "args": "takes no command-line arguments",
    "config": "has no config file",
    "paths": "takes no file names",
    "files": "reads no data files",
    "csv": "reads no CSV file a person saved, only pprl files",
    "pprl-files": "reads no pprl file",
    "contents": "checks no file's columns or values",
    "output": "writes no files",
    "bundle": "reads no bundle",
}

# The role whose workspace each pprl command runs in. Also the inputs it reads
# there, by their fixed names, copied from the inputs (see build_inputs), and
# the inputs left out because the command would write them.
ROLES = {
    "link": "linkage",
    "merge": "merge",
    **dict.fromkeys(("synth", "bundles", "guides", "sweep"), "coordinator"),
}
FIXED_INPUTS = {"clean": {"patients.csv": "patients_a.csv"}}
LEFT_OUT = {"select": ("site_a.selected.pprl",), "clean": ("site_a.clean.pprl",)}


def role_of(command: str) -> str:
    """Return the role of the workspace a command's cases run in."""
    return ROLES.get(command, "site")


# ---------------------------------------------------------------------------
# Rows: kinds of input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Input:
    """A kind of input in the grid: its row."""

    key: str
    section: str
    title: str
    # A kind of input from Command.takes, or the command (or tuple of commands)
    # that alone has this input.
    needs: str | tuple


INPUTS = {
    i.key: i
    for i in (
        Input("no-command", "Command line", "No command", "cli"),
        Input("unknown-command", "Command line", "Unknown command", "cli"),
        Input("version", "Command line", "Asking for the version", "cli"),
        Input("no-arguments", "Command line", "Required arguments missing", "args"),
        Input(
            "bundle-missing", "Bundle", "Bundle missing from the workspace", ("hash",)
        ),
        Input(
            "bundle-changed", "Bundle", "Bundle changed after it was written", ("hash",)
        ),
        Input("bundle-other", "Bundle", "Bundle of another workspace", ("hash",)),
        Input("setting-missing", "Bundle", "Setting missing from the bundle", "bundle"),
        Input("config-missing", "Config file", "Config file not found", "config"),
        Input("config-empty", "Config file", "Config file empty", ("bundles", "synth")),
        Input(
            "config-invalid",
            "Config file",
            "Config file not valid YAML",
            ("bundles", "synth"),
        ),
        Input(
            "config-not-mapping",
            "Config file",
            "Config is not a mapping",
            ("bundles", "synth"),
        ),
        Input(
            "config-outside", "Config file", "Config outside the workspace", "config"
        ),
        Input("unknown-key", "Config file", "Unknown key", "config"),
        Input("reserved-key", "Config file", "Reserved key", "config"),
        Input("missing-key", "Config file", "Required key missing", "config"),
        Input("wrong-type", "Values", "Wrong type", "config"),
        Input("out-of-range", "Values", "Number out of range", "config"),
        Input("unknown-choice", "Values", "Unsupported choice", "config"),
        Input("every-choice", "Values", "Every supported choice", "config"),
        Input("too-few", "Values", "Too few items in a list", "config"),
        Input(
            "outside-path",
            "Values",
            "Absolute path, or one out of the workspace (../)",
            "paths",
        ),
        Input("input-missing", "Files", "Input file not found", "files"),
        Input("input-empty", "Files", "Input file empty (0 bytes)", "files"),
        Input("input-malformed", "Files", "Malformed CSV", ("clean", "inspect")),
        Input(
            "not-utf8", "Files", "Not UTF-8 (such as from Excel)", ("clean", "inspect")
        ),
        Input(
            "saved-variants",
            "Files",
            "Byte-order mark, Windows line endings",
            ("clean", "inspect"),
        ),
        Input(
            "blank-rows", "Files", "Empty rows within the file", ("clean", "inspect")
        ),
        Input(
            "ragged-rows",
            "Files",
            "Rows with too few or too many values",
            ("clean", "inspect"),
        ),
        Input(
            "wrong-separator", "Files", "Separated by semicolons", ("clean", "inspect")
        ),
        Input(
            "column-names",
            "Files",
            "Columns unnamed, repeated, or with spaces in the name",
            ("clean", "inspect"),
        ),
        Input("output-exists", "Files", "Output already exists", "output"),
        Input("not-pprl", "pprl files", "A file that is not a pprl file", ("hash",)),
        Input(
            "pprl-damaged",
            "pprl files",
            "A pprl file opened and saved in a spreadsheet or editor",
            ("hash", "inspect"),
        ),
        Input("pprl-other-kind", "pprl files", "Another kind of pprl file", ("hash",)),
        Input(
            "pprl-bundle",
            "pprl files",
            "A bundle where a data file belongs",
            ("hash",),
        ),
        Input(
            "pprl-other-project",
            "pprl files",
            "A file of another project",
            ("hash",),
        ),
        Input(
            "pprl-other-set",
            "pprl files",
            "A file made with an earlier set of bundles",
            ("hash",),
        ),
        Input("pprl-other-site", "pprl files", "Another site's file", ("hash",)),
        Input(
            "pprl-other-release",
            "pprl files",
            "A file written by another release",
            ("hash", "inspect"),
        ),
        Input("header-only", "File contents", "Header but no rows", "contents"),
        Input("missing-column", "File contents", "Required column missing", "contents"),
        Input("duplicate-id", "File contents", "Duplicate id", "contents"),
        Input("extra-columns", "File contents", "Extra columns", "contents"),
        Input(
            "bad-format",
            "File contents",
            "Value not in its format (date, ZIP, state)",
            "contents",
        ),
        Input("valid", "Valid input", "Typical valid input", "any"),
        Input("scenario", "Command-specific", "Scenario mistakes", "synth"),
        Input(
            "repairs", "Command-specific", "Values clean repairs or sets aside", "clean"
        ),
        Input("hash-length", "Command-specific", "Hash lengths differ", "link"),
        Input(
            "different-secrets",
            "Command-specific",
            "Hashed with different secrets",
            "link",
        ),
        Input(
            "selection",
            "Command-specific",
            "Rows the project's rules leave out, or should have",
            ("select", "clean", "pack"),
        ),
        Input(
            "unknown-ids",
            "Command-specific",
            "Rows whose patient has no study_id",
            "pack",
        ),
        Input(
            "other-selection",
            "Command-specific",
            "Cleaned file of another run of select",
            "pack",
        ),
        Input("merge-config", "Command-specific", "Table config mistakes", "merge"),
        Input(
            "other-run",
            "Command-specific",
            "Tables and hashes from different runs of clean",
            "merge",
        ),
        Input("merge-data", "Command-specific", "Site data mistakes", "merge"),
        Input("project", "Command-specific", "Project mistakes", ("bundles", "guides")),
        Input("deployment", "Command-specific", "Deployment mistakes", "guides"),
        Input("assessment", "Command-specific", "Assessment", "sweep"),
    )
}


def _na(input_key: str, reason: str, *commands: str) -> dict:
    return {(input_key, command): reason for command in commands}


# Cells that the command's inputs (Command.takes) would allow, but where this
# kind of input doesn't exist for the command.
NOT_APPLICABLE = {
    **_na("unknown-key", "the scenario itself is the config", "synth"),
    **_na("reserved-key", "the scenario itself is the config", "synth"),
    **_na(
        "config-outside",
        "reads project.yml, in the workspace, and no file it is given",
        "bundles",
    ),
    **_na(
        "config-outside",
        "reads synth.yml, in the workspace, and no file it is given",
        "synth",
    ),
    **_na(
        "outside-path",
        "reads synth.yml, in the workspace, which names no files",
        "synth",
    ),
    **_na(
        "config-outside",
        "reads sweep.yml, in the workspace, and no file it is given",
        "sweep",
    ),
    **_na(
        "config-outside",
        "reads deployment.yml and project.yml, in the workspace, and no file it is "
        "given",
        "guides",
    ),
    **_na("unknown-choice", "no setting with a fixed set of choices", "guides"),
    **_na("too-few", "no list settings", "guides"),
    **_na(
        "unknown-choice",
        "no setting with a fixed set of choices",
        "sweep",
    ),
    **_na(
        "every-choice",
        "no setting with a fixed set of choices",
        "sweep",
    ),
    **_na("too-few", "no list settings", "synth"),
    **_na("setting-missing", "reads no settings", "inspect"),
    **_na(
        "bad-format",
        "reads no dates, ZIP codes or states",
        "link",
        "inspect",
    ),
    **_na("duplicate-id", "reads no patient or hash files", "merge"),
    **_na("bad-format", "reads no dates, ZIP codes or states", "select", "pack"),
    **_na("extra-columns", "reads only the columns it describes", "inspect"),
}

# Cells that apply but are not tested yet.
GAPS = {}


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class _Drop:
    def __repr__(self) -> str:
        return "DROP"


DROP = _Drop()  # In Case.config or Case.files: remove this key or file


@dataclass(frozen=True)
class Case:
    """One input given to one command, and what pprl does with it.

    The command runs through pprl's command line, in a fresh workspace for
    its role (see ``role_of``). The workspace holds a copy of the inputs
    (see ``build_inputs``), with the ones the command reads under their
    fixed names (see FIXED_INPUTS).

    A pprl command's settings come from the workspace's bundle. The bundle
    holds the role's test settings, updated with
    ``bundle``, unless ``bundle_text`` gives the whole file (DROP means no
    bundle). A command on the project reads a config instead. The config is
    DEFAULT_CONFIGS[command] updated with ``config``, written to the name
    the command reads when given none (see CONFIG_NAMES), unless
    ``config_text`` gives the whole file (DROP means no file).

    ``files`` first writes files in the workspace, or removes them with
    DROP. A file's text can also be bytes, or a function of the workspace's
    path (see ``edited``). "{workspace}" in ``argv`` or in a config value
    stands for the absolute path of the workspace.

    The outcome is ``accepted`` (exit code 0), rejected (exit code 1), or
    ``crashes`` (an uncaught exception of that type). In every case, the
    output (or the exception's message) must contain ``says``. That is a
    text, a Message from the catalog (with any details in its
    placeholders), or a tuple of these, which must all be shown.
    """

    command: str
    input: str
    did: str  # What the user did, in a few words
    says: str | Message | tuple
    config: dict = field(default_factory=dict)
    config_text: str | _Drop | None = None
    bundle: dict = field(default_factory=dict)
    bundle_text: str | bytes | Callable[[Path], str | bytes] | _Drop | None = None
    role: str | None = None  # The workspace's role, if not the command's
    files: dict = field(default_factory=dict)
    argv: tuple | None = None
    accepted: bool = False
    crashes: str | None = None
    creates: str | None = None  # A file that must exist afterward
    problem: str | None = None  # Why the current handling falls short

    @property
    def outcome(self) -> str:
        """Return the case's outcome, as the grid marks it."""
        if self.crashes:
            return f"crashes ({self.crashes})"
        return "accepted" if self.accepted else "rejected"

    @property
    def says_all(self) -> tuple:
        """Return the messages and texts that the output must show."""
        return self.says if isinstance(self.says, tuple) else (self.says,)

    @property
    def says_markdown(self) -> str:
        """Format ``says`` for the review table: a message's text, or the texts."""
        messages = [s for s in self.says_all if isinstance(s, Message)]
        if messages:
            return " ".join(markdown_cell(m.text) for m in messages)
        return f"`{markdown_cell(' / '.join(self.says_all))}`"


# The config file each command reads by default, and its valid contents.
CONFIG_NAMES = {
    command: f"{command}.yml" for command, c in COMMANDS.items() if "config" in c.takes
} | {"bundles": "project.yml", "guides": "deployment.yml"}

DEFAULT_CONFIGS = {
    "synth": {
        "seed": 2026,
        "sites": ["site_a", "site_b"],
        "outputs": ["patients"],
        "generated_individuals": [{"sites": ["site_a", "site_b"], "count": 5}],
    },
    "bundles": PROJECT_SETTINGS,
    "guides": read_config(EXAMPLES / DEPLOYMENT_FILE),
    "sweep": {
        "hashes": ["site_a.hashes.pprl", "site_b.hashes.pprl"],
        "thresholds": [0.90, 0.95],
        "output": "sweep.csv",
    },
}


# Config files a command reads besides its own. guides also reads project.yml.
OTHER_CONFIGS = {"guides": {"project.yml": DEFAULT_CONFIGS["bundles"]}}


def build_inputs(folder: Path) -> None:
    """Fill ``folder`` with the valid files every case starts from.

    patients_a.csv, patients_b.csv  two sites sharing 8 individuals, as
                                    synth writes them, keyed by person_id
    tables/scores.csv               site_a's table for select, by person_id
    site_a.selected.pprl, site_b... each site's table, every row selected
    site_a.clean.pprl, site_b...    each site's cleaned patient file, made
                                    from it (with person_ids as study_ids:
                                    see write_cleaned)
    site_a.hashes.pprl, site_b...   their hashes
    512.hashes.pprl                 site_b's hashes with 512 bits, not 1024
    other_secret.hashes.pprl        site_b's hashes, made with another secret
    linkages.pprl                   the two sites linked
    site_a.tables.pprl, site_b...   each site's table as pack writes it
    """
    folder = Path(folder)
    staging = folder.parent / f"{folder.name}_staging"
    rc = run_scenario(staging, two_site_scenario(shared=8, only_a=2, only_b=2))
    assert rc == 0, "Could not build the usability inputs"
    folder.mkdir(parents=True, exist_ok=True)
    cleaned = {}
    for site, suffix in (("site_a", "a"), ("site_b", "b")):
        patients = read_csv_frame(staging / "scenario" / site / "patients.csv")
        write_csv_frame(patients, folder / f"patients_{suffix}.csv")
        scores = patients[[SYNTH_ID]].copy()
        scores["score"] = [str(len(name)) for name in patients["name"]]
        selected = write_selected(
            folder / file_name("selected", site), site, {"scores": scores}
        )
        write_cleaned(patients, folder / file_name("clean", site), site, selected)
        cleaned[site] = _pprl_file.read(folder / file_name("clean", site))
        if site == "site_a":
            (folder / "tables").mkdir()
            write_csv_frame(scores, folder / "tables" / "scores.csv")
        _pprl_file.write(
            folder / file_name("tables", site),
            "tables",
            {"scores": as_cleaned(scores)},
            provenance=provenance(site),
            inputs=[cleaned[site]],
        )
    for site, output, secret in (
        ("site_a", file_name("hashes", "site_a"), SYNTH_SECRET),
        ("site_b", file_name("hashes", "site_b"), SYNTH_SECRET),
        ("site_b", "other_secret.hashes.pprl", "9" * 64),
    ):
        rc = _hash.hash_records(
            patients=file_name("clean", site),
            secret=secret,
            fields=SYNTH_FIELDS,
            output=output,
            **HASH_SIZES,
            provenance=provenance(site),
            **call_kwargs(folder),
        )
        assert rc == 0, f"Could not hash {output}"
    # Hashes of another length, which pprl itself no longer writes.
    hashes = _pprl_file.read(folder / file_name("hashes", "site_b")).members["hashes"]
    _pprl_file.write(
        folder / "512.hashes.pprl",
        "hashes",
        {
            "hashes": hashes.assign(
                hash=[
                    serialize_bitarray(deserialize_bitarray(clk)[:512])
                    for clk in hashes["hash"]
                ]
            )
        },
        provenance=provenance("site_b"),
    )
    rc = _link.link(
        hashes=[file_name("hashes", site) for site in ("site_a", "site_b")],
        threshold=0.95,
        output=file_name("linkages"),
        sites=["site_a", "site_b"],
        provenance=provenance(),
        **call_kwargs(folder),
    )
    assert rc == 0, "Could not link the inputs"
    shutil.rmtree(staging)


def edited(
    name: str, change: Callable[[pd.DataFrame], pd.DataFrame]
) -> Callable[[Path], str]:
    """Make file text from the workspace's CSV file ``name``, changed.

    The file is read as a DataFrame and passed through ``change``.
    """

    def text(folder: Path) -> str:
        df = change(read_csv_frame(Path(folder) / name))
        return df.to_csv(index=False, lineterminator="\n")

    return text


def repacked(
    name: str,
    change: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    member: str | None = None,
    **header: object,
) -> Callable[[Path], bytes]:
    """Make file bytes by rewriting the workspace's pprl file ``name`` with changes.

    Its ``member`` (by default its only CSV member) is passed through
    ``change``. ``header`` replaces any of the header's project, set, site,
    kind or release. Only a deliberate edit could produce such a file.
    """

    def data(folder: Path) -> bytes:
        original = _pprl_file.read(Path(folder) / name, check_release=False)
        members = dict(original.members)
        if change is not None:
            key = member or next(iter(members))
            members[key] = change(members[key])
        staged = Path(folder) / ".repacked.pprl"
        kept = ("kind", "project", "set", "site", "inputs")
        write_again(staged, members, {k: original.header[k] for k in kept} | header)
        result = staged.read_bytes()
        staged.unlink()
        return result

    return data


@overload
def _fill(value: str, folder: Path) -> str: ...
@overload
def _fill(value: list, folder: Path) -> list: ...
@overload
def _fill(value: dict, folder: Path) -> dict: ...
@overload
def _fill(value: object, folder: Path) -> object: ...
def _fill(value: object, folder: Path) -> object:
    """Return ``value`` with "{workspace}" in its strings replaced by the path."""
    if isinstance(value, str):
        return value.replace("{workspace}", str(folder))
    if isinstance(value, list):
        return [_fill(v, folder) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, folder) for k, v in value.items()}
    return value


def prepare_case(case: Case, inputs: Path, folder: Path) -> list[str]:
    """Fill the workspace ``folder`` for ``case``, and return the command line to run.

    The workspace gets a copy of ``inputs``, with the command's inputs under
    their fixed names, its bundle or config, and the case's files. The
    command line leaves out the program's name.
    """
    folder = Path(folder)
    _copy_inputs(case, inputs, folder)
    if "bundle" in COMMANDS[case.command].takes:
        _write_case_bundle(case, folder)
    # The other config file a command reads, as the cases leave it.
    for name, config in OTHER_CONFIGS.get(case.command, {}).items():
        write_yaml(folder / name, config)
    _write_case_files(case, folder)
    _write_case_config(case, folder)
    if case.argv is None:
        return [case.command]  # The command reads its default config file.
    return _fill(list(case.argv), folder)


def _copy_inputs(case: Case, inputs: Path, folder: Path) -> None:
    """Copy ``inputs`` to ``folder``, with the command's inputs under fixed names.

    Inputs that the case leaves out are not copied.
    """
    shutil.copytree(inputs, folder, dirs_exist_ok=True)
    for name, source in FIXED_INPUTS.get(case.command, {}).items():
        if name != source:
            shutil.copy(folder / source, folder / name)
    for name in LEFT_OUT.get(case.command, ()):
        path = folder / name
        shutil.rmtree(path) if path.is_dir() else path.unlink()


def _write_case_bundle(case: Case, folder: Path) -> None:
    """Write the bundle for ``case``, then replace or drop it as the case says."""
    write_bundle(folder, **case.bundle)
    marker = read_workspace(folder)
    assert marker.bundle_name is not None  # The case's role has a bundle.
    path = folder / marker.bundle_name
    text = case.bundle_text
    if text is DROP:
        path.unlink()
    elif text is not None:
        content = text(path) if callable(text) else text
        path.write_bytes(
            content if isinstance(content, bytes) else str(content).encode()
        )


def _write_case_files(case: Case, folder: Path) -> None:
    """Write or drop each of the case's own files."""
    for name, text in case.files.items():
        path = folder / name
        if text is DROP:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        content = text(folder) if callable(text) else text
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            write_text(path, content, replace=True)


def _write_case_config(case: Case, folder: Path) -> None:
    """Write the config file that the command reads, as the case gives it."""
    config_name = CONFIG_NAMES.get(case.command)
    if case.config_text is DROP:
        return
    if isinstance(case.config_text, str):
        assert config_name is not None  # Only commands that read a config get here.
        write_text(folder / config_name, case.config_text)
    elif config_name:
        config = {**DEFAULT_CONFIGS[case.command], **case.config}
        config = {k: _fill(v, folder) for k, v in config.items() if v is not DROP}
        write_yaml(folder / config_name, config)


def run_case(case: Case, inputs: Path, folder: Path) -> tuple[object, str]:
    """Run ``case`` in a workspace at ``folder``/workspace holding a copy of ``inputs``.

    Return (exit code or exception, everything printed).
    """
    workspace = Path(folder) / "workspace"
    make_workspace(workspace, role=case.role or role_of(case.command))
    argv = prepare_case(case, inputs, workspace)
    out = io.StringIO()
    with (
        cli_in(workspace),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(out),
    ):
        try:
            result = main(argv)
        except SystemExit as e:
            result = e.code
        except Exception as e:  # noqa: BLE001 (the case may expect a crash)
            result = e
    return result, strip_ansi(out.getvalue())


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


def cell_status(
    input_key: str, command: str, cases: list[Case]
) -> tuple[str | None, Any]:
    """Return a cell's status: ("covered", cases), ("n/a", reason), ("gap", reason).

    Return (None, None) if the cell is outside the grid, meaning the input
    belongs only to another command.
    """
    row, column = INPUTS[input_key], COMMANDS[command]
    owners = row.needs if isinstance(row.needs, tuple) else (row.needs,)
    if owners[0] in COMMANDS:
        if command not in owners:
            return None, None
    elif row.needs != "any" and row.needs not in column.takes:
        assert isinstance(row.needs, str)  # A tuple would name commands.
        return "n/a", f"{command} {_TAKES[row.needs]}"
    covering = [c for c in cases if c.input == input_key and c.command == command]
    if covering:
        return "covered", covering
    if (input_key, command) in NOT_APPLICABLE:
        return "n/a", NOT_APPLICABLE[(input_key, command)]
    if (input_key, command) in GAPS:
        return "gap", GAPS[(input_key, command)]
    return "missing", None


def grid_problems(cases: list[Case], cli_commands: Iterable[str]) -> list[str]:
    """List every way the grid is incomplete or inconsistent, as sentences."""
    problems = [
        f"{command}: neither a column of the grid nor in OUTSIDE_GRID"
        for command in cli_commands
        if command not in COMMANDS and command not in OUTSIDE_GRID
    ]
    problems.extend(
        f"{command}: in the grid, but pprl has no such command"
        for command in [*COMMANDS.keys() - {"pprl"}, *OUTSIDE_GRID]
        if command not in cli_commands
    )
    problems.extend(
        f"{command}: both a column of the grid and in OUTSIDE_GRID"
        for command in COMMANDS.keys() & OUTSIDE_GRID.keys()
    )
    for case in cases:
        if case.input not in INPUTS or case.command not in COMMANDS:
            problems.append(f"Unknown cell for a case: {case.command} / {case.input}")
            continue
        status, _ = cell_status(case.input, case.command, [])
        explicit = (case.input, case.command) in NOT_APPLICABLE
        if status is None or (status == "n/a" and not explicit):
            problems.append(
                f"{case.command} / {case.input}: a case for a cell that does not apply"
            )
    problems.extend(
        f"Unknown cell in NOT_APPLICABLE or GAPS: {key}"
        for key in [*NOT_APPLICABLE, *GAPS]
        if key[0] not in INPUTS or key[1] not in COMMANDS
    )
    problems.extend(
        f"{key[1]} / {key[0]}: in both NOT_APPLICABLE and GAPS"
        for key in NOT_APPLICABLE.keys() & GAPS.keys()
    )
    for input_key in INPUTS:
        for command in COMMANDS:
            key = (input_key, command)
            status, _ = cell_status(input_key, command, cases)
            if status == "missing":
                problems.append(
                    f"{command} / {input_key}: needs a case, or an entry in "
                    f"NOT_APPLICABLE or GAPS"
                )
            elif status == "covered" and (key in NOT_APPLICABLE or key in GAPS):
                problems.append(
                    f"{command} / {input_key}: has cases, but is also in "
                    f"NOT_APPLICABLE or GAPS"
                )
    return problems


# ---------------------------------------------------------------------------
# The review table of the grid and its cases
# ---------------------------------------------------------------------------

_SYMBOLS = {
    "accepted": "✓",
    "rejected": "✗",
    "crashes": "💥",
    "n/a": "-",
    "gap": "?",
}


def _cell_symbol(status: str | None, value: object) -> str:
    if status == "covered":
        cases = cast("list[Case]", value)  # A covered cell's value is its cases.
        outcomes = []
        for case in cases:
            symbol = _SYMBOLS[case.outcome.split()[0]]
            if symbol not in outcomes:
                outcomes.append(symbol)
        mark = "".join(outcomes)
        return mark + ("⚠" if any(case.problem for case in cases) else "")
    return _SYMBOLS.get(status, "")


def _problem_lines(cases: list[Case]) -> list[str]:
    """Write the "Known problems" section: each problem and its cases."""
    problems = {}  # Maps each problem to the cases that show it, in order
    for case in cases:
        if case.problem:
            problems.setdefault(case.problem, []).append(case)
    if not problems:
        return []
    lines = ["", "## Known problems", ""]
    for problem, showing in problems.items():
        lines.append(f"- {problem}:")
        lines += [f"  - `{case.command}` {case.did}" for case in showing]
    return lines


def _command_lines(command: str, cases: list[Case]) -> list[str]:
    """Write a command's section, with a row for each case and gap.

    There is also a row for each input that does not apply to the command.
    """
    lines = ["", f"## `{command}`", "", COMMANDS[command].summary + ".", ""]
    lines += ["| Input | What the user did | Result | What pprl says |"]
    lines += ["| --- | --- | --- | --- |"]
    for row in INPUTS.values():
        status, value = cell_status(row.key, command, cases)
        if status == "covered":
            for case in value:
                result = case.outcome + (" ⚠" if case.problem else "")
                lines.append(
                    f"| {row.title} | {markdown_cell(case.did)} | {result} | "
                    f"{case.says_markdown} |"
                )
        elif status == "gap" or (
            status == "n/a" and (row.key, command) in NOT_APPLICABLE
        ):
            lines.append(f"| {row.title} | | {status} | {markdown_cell(value)} |")
    return lines


def render_markdown(cases: list[Case]) -> str:
    """Render the review table of the grid and every case, as Markdown."""
    commands = list(COMMANDS)
    lines = [
        (
            "Every row is a kind of input a user might give, and every column a "
            "command. Each mark stands for one or more test cases that run the "
            "input through the command line and check what the user sees."
        ),
        "",
        (
            "✓ accepted · ✗ rejected with a message · 💥 crashes with a Python "
            "error · ⚠ handled, but not as it should be · - does not apply · "
            "? not tested yet"
        ),
        "",
        (
            "Where pprl shows a message from src/pprl/_messages.py, the table gives "
            "its text, with {placeholders} for the details of each case."
        ),
        "",
        "| Input | " + " | ".join(f"`{c}`" for c in commands) + " |",
        "| --- |" + " :---: |" * len(commands),
    ]
    section = None
    for row in INPUTS.values():
        if row.section != section:
            section = row.section
            lines.append(f"| **{section}** |" + " |" * len(commands))
        cells = [_cell_symbol(*cell_status(row.key, c, cases)) for c in commands]
        lines.append(f"| {row.title} | " + " | ".join(cells) + " |")

    lines += _problem_lines(cases)
    for command in commands:
        lines += _command_lines(command, cases)
    return markdown_page(
        "How pprl handles each kind of input",
        "tests/usability_cases.py",
        "tests/usability_grid.py",
        lines,
    )
