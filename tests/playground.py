"""Try pprl by hand, in a workspace, as a user would (see `make help` or --help).

    python -m tests.playground list                  every usability case
    python -m tests.playground try hash/12           set up one case, then run it
    python -m tests.playground demo --size medium    every role, start to finish
    python -m tests.playground demo --messy          ... with raw exports to clean
    python -m tests.playground reset                 empty the playground

Each command really runs pprl, in your terminal, so you see what a user sees:
progress bars, colors, and the messages for every problem.

The playground lives in build/playground/. It holds one workspace for each
role (coordinator, site_a, site_b, linkage and merge), all in one project.
The playground deletes that folder only if it made the folder itself, so it
never deletes anything else.
"""

import argparse
import random
import shutil
import signal
import sys
from pathlib import Path

from pprl import _bundle, _workspace
from pprl import _messages as msg
from pprl._io import read_csv_frame, write_csv_frame, write_text
from pprl._shared import OUTBOX, log_name
from pprl._ui import silenced
from tests.commands import DESTROY_YES, run_text
from tests.helpers import (
    PROJECT_SETTINGS,
    SOURCE_ROOT,
    make_workspace,
    write_yaml,
)
from tests.usability_cases import CASES
from tests.usability_grid import build_inputs, prepare_case, role_of

PLAYGROUND = SOURCE_ROOT / "build" / "playground"
OURS = PLAYGROUND / ".playground"  # Marks the folder as made by the playground
# Each role's workspace in the playground, by name: (role, site).
WORKSPACES = {
    "coordinator": ("coordinator", None),
    "site_a": ("site", "site_a"),
    "site_b": ("site", "site_b"),
    "linkage": ("linkage", None),
    "merge": ("merge", None),
}
# The workspace that each role's usability cases run in.
ROLE_WORKSPACES = {
    "coordinator": "coordinator",
    "site": "site_a",
    "linkage": "linkage",
    "merge": "merge",
}
# The project that the playground's workspaces belong to.
PROJECT = "00000000-0000-4000-8000-00000000abcd"
INPUTS = SOURCE_ROOT / "tests" / "tmp" / "playground-inputs"
INTERRUPTED = 130  # The exit code of a run stopped with Ctrl+C

# Individuals per demo size: (at both sites, at only one site, for each site).
SIZES = {
    "small": (800, 200),  # 1,000 records per site: every step is instant
    "medium": (20_000, 5_000),  # 25,000: synth takes about 30 seconds
    "large": (100_000, 25_000),  # 125,000: synth a few minutes, link about a minute
}


# ---------------------------------------------------------------------------
# The playground's workspaces
# ---------------------------------------------------------------------------


def _shown(folder: Path) -> str:
    return folder.relative_to(SOURCE_ROOT).as_posix()


def _ours(folder: Path) -> bool:
    """Tell whether the playground made this workspace."""
    try:
        return _workspace.read(folder).project == PROJECT
    except _workspace.WorkspaceError:
        return False


def reset() -> None:
    """Empty the playground.

    The demo creates each role's workspace with pprl init, as people will. A
    usability case creates the workspace it runs in (see try_case).
    """
    if PLAYGROUND.exists() and not (OURS.exists() or _ours(PLAYGROUND)):
        sys.exit(
            f"{_shown(PLAYGROUND)}/ was not made by the playground. Move it "
            "somewhere else first: the playground deletes that folder."
        )
    shutil.rmtree(PLAYGROUND, ignore_errors=True)
    PLAYGROUND.mkdir(parents=True)
    write_text(OURS, "This folder is the playground's: see tests/playground.py\n")


def _run(folder: Path, *argv: str, answers: str | None = None) -> str:
    """Run pprl with ``argv`` in ``folder``, as a user would.

    Type ``answers`` if pprl asks. Return the command as shown.
    """
    shown = " ".join(str(a).replace(str(folder), ".") for a in argv)
    pprl = shutil.which("pprl", path=str(Path(sys.executable).parent))
    if pprl is None:  # Never: it is installed next to this Python, with the project.
        raise AssertionError
    command = [pprl]
    typed = f"uv run pprl {shown}"
    print(f"\n\033[1m{_shown(folder)}$ {typed}\033[0m", flush=True)
    # Leave Ctrl+C to pprl while it runs, as a shell does: pprl reports it, and
    # then the playground stops. Install a handler that does nothing, rather
    # than ignoring the signal, because pprl would inherit an ignored signal.
    previous = signal.signal(signal.SIGINT, lambda _signum, _frame: None)
    try:
        result = run_text(
            [*command, *map(str, argv)],
            cwd=folder,
            input=answers,
        )
    finally:
        signal.signal(signal.SIGINT, previous)
    if result.returncode in (INTERRUPTED, -signal.SIGINT):  # Stopped either way
        sys.exit(INTERRUPTED)
    return typed


def _pprl(name: str, *argv: str) -> str:
    """Run pprl in the workspace ``name``."""
    return _run(PLAYGROUND / name, *argv)


# ---------------------------------------------------------------------------
# Usability cases
# ---------------------------------------------------------------------------


def _case_ids() -> dict:
    """Map each case id to its command and its position among that command's cases."""
    ids, counts = {}, {}
    for case in CASES:
        counts[case.command] = counts.get(case.command, 0) + 1
        ids[f"{case.command}/{counts[case.command]}"] = case
    return ids


def list_cases(command: str | None = None) -> None:
    """List the usability cases, only for ``command`` if given."""
    for case_id, case in _case_ids().items():
        if command in (None, case.command):
            print(f"{case_id:<14} {case.outcome:<10} {case.did}")


def try_case(case_id: str) -> None:
    """Set up the playground for one usability case, then run it."""
    cases = _case_ids()
    if case_id not in cases:
        sys.exit(
            f"No case {case_id}. Run `make try` "
            "(or `python -m tests.playground list`) to list them."
        )
    case = cases[case_id]
    if not INPUTS.exists():
        print("Building the files the cases start from (once)...")
        with silenced():
            build_inputs(INPUTS)
    reset()
    role = case.role or role_of(case.command)
    folder = PLAYGROUND / ROLE_WORKSPACES[role]
    make_workspace(folder, role=role)
    outside_before = set(folder.parent.iterdir())
    argv = prepare_case(case, INPUTS, folder)
    print(f"\n{case_id}: the user {case.did}.")
    print(f"pprl should say this is {case.outcome}.")
    try:
        shown = _run(folder, *argv)
    finally:
        # Cases about paths outside the workspace put a file next to it.
        for path in set(folder.parent.iterdir()) - outside_before:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    print(
        f"\n{_shown(folder)}/ holds this case's files: open a terminal there, "
        f"change one, and run {shown} again."
    )


# ---------------------------------------------------------------------------
# The demo: every role, start to finish
# ---------------------------------------------------------------------------


def _messy(path: Path, seed: int, *, windows_text: bool) -> None:
    """Rewrite a patient file to look like a raw export.

    Add values that clean repairs (spaces, ZIP+4 codes, lowercase states,
    times after dates) and values that it sets aside (US dates, 4-digit ZIP
    codes, states in full, repeated person_ids, rows with no identifying
    value). With ``windows_text``, save the file as Windows text, which
    clean converts.
    """
    rng = random.Random(seed)
    df = read_csv_frame(path)
    changes = (
        ("zip", lambda v: f"{v}-{rng.randint(1000, 9999)}"),
        ("zip", lambda v: f" {v} "),
        ("zip", lambda v: v.removeprefix("0")),
        ("state", str.lower),
        ("state", lambda v: "Massachusetts" if v == "MA" else v),
        ("dob", lambda v: f"{v} 00:00:00"),
        ("dob", lambda v: f"{v[5:7]}/{v[8:10]}/{v[:4]}" if v else v),
        ("name", lambda v: v.replace("e", "é", 1)),
    )
    for index in df.index:
        if rng.random() < 0.15:
            column, change = rng.choice(changes)
            df.at[index, column] = change(df.at[index, column])
    for index in rng.sample(list(df.index), max(1, len(df) // 200)):
        df.loc[index, ["name", "city", "state", "zip", "dob"]] = ""
    repeated = rng.sample(list(df.index), max(2, len(df) // 500))
    df.loc[repeated[1:], "person_id"] = df.at[repeated[0], "person_id"]
    text = df.to_csv(index=False, lineterminator="\r\n")
    path.write_bytes(
        text.encode("cp1252", errors="replace") if windows_text else text.encode()
    )


def demo(size: str, *, messy: bool, keep: bool = False) -> None:
    """Run a project, with each role in its own workspace.

    1. The coordinator makes test data and the bundles.
    2. Each role creates its workspace from its bundle.
    3. The coordinator locks the secret to each site's public key.
    4. site_a and site_b select, clean, hash and pack.
    5. The linkage agent links.
    6. The merge agent merges the sites' score tables.
    7. Each role destroys its workspace (unless ``keep``), leaving its
       receipt.
    """
    reset()
    sites = ["site_a", "site_b"]
    if not (_demo_coordinator(size, sites) and _demo_workspaces(sites)):
        return
    _demo_secrets(sites)
    _demo_site_data(sites, messy=messy)
    _demo_sites(sites)
    print("\n== The linkage agent links the hash files")
    _pprl("linkage", "link")
    _pprl("linkage", "inspect", f"{OUTBOX}/merge/linkages.pprl")
    print("\n== The coordinator reviews the linkage, from the linkage report")
    shutil.copy(
        PLAYGROUND / "linkage" / OUTBOX / "coordinator" / "linkage_report.pprl",
        PLAYGROUND / "coordinator",
    )
    _pprl("coordinator", "review")
    print("\n== The merge agent merges the sites' tables")
    shutil.copy(
        PLAYGROUND / "linkage" / OUTBOX / "merge" / "linkages.pprl",
        PLAYGROUND / "merge",
    )
    _pprl("merge", "merge")
    if keep:
        print(
            f"\nEvery workspace is in {_shown(PLAYGROUND)}/; `make reset` "
            "(or `python -m tests.playground reset`) empties it."
        )
        return
    print("\n== At the end of the project, each role destroys its workspace")
    for name in (*sites, "linkage", "merge", "coordinator"):
        _run(PLAYGROUND / name, "destroy", answers=DESTROY_YES)
    print(
        f"\nEach role's receipt is in its outbox/coordinator/, in "
        f"{_shown(PLAYGROUND)}/."
    )


def _demo_coordinator(size: str, sites: list[str]) -> bool:
    """Make the coordinator's workspace, test data and bundles.

    Return False if a step failed (after reporting it).
    """
    coordinator = PLAYGROUND / "coordinator"
    shared, only_one = SIZES[size]
    print("\n== The coordinator writes project.yml, and creates its workspace")
    coordinator.mkdir()
    # The test project's settings, as examples. A real project chooses its own.
    write_yaml(
        coordinator / "project.yml",
        PROJECT_SETTINGS | {"name": f"Playground ({size})", "sites": sites},
    )
    _pprl("coordinator", "init")
    if not (coordinator / "coordinator.log").exists():
        return False
    write_yaml(
        coordinator / "synth.yml",
        {
            "seed": f"playground-{size}",
            "sites": sites,
            "outputs": ["patients"],
            "generated_individuals": [
                {"sites": sites, "count": shared},
                {"sites": ["site_a"], "count": only_one},
                {"sites": ["site_b"], "count": only_one},
            ],
            "pii": {
                "name": {"typos": {"rate": 0.05, "max_edits": 1}},
                "address": {"typos": {"rate": 0.05, "max_edits": 1}},
            },
        },
    )

    print(f"\n== The coordinator makes test data ({size}) and every bundle")
    _pprl("coordinator", "synth")
    if not (coordinator / "synth").exists():
        return False
    _pprl("coordinator", "bundles")
    return (coordinator / OUTBOX / "linkage" / "linkage.pprl").exists()


def _demo_workspaces(sites: list[str]) -> bool:
    """Make each other role's workspace from its bundle. Return False if one fails."""
    coordinator = PLAYGROUND / "coordinator"
    for name in (*sites, "linkage", "merge"):
        role, site = WORKSPACES[name]
        print(f"\n== {msg.owner(role, site)} creates a workspace from its bundle")
        bundle = _bundle.bundle_name(role, site)
        (PLAYGROUND / name).mkdir()
        shutil.copy(coordinator / OUTBOX / name / bundle, PLAYGROUND / name / bundle)
        _pprl(name, "init")
        if not (PLAYGROUND / name / log_name(role, site)).exists():
            return False
    return True


def _demo_secrets(sites: list[str]) -> None:
    """Send the sites' public keys, and lock the secret to each one."""
    coordinator = PLAYGROUND / "coordinator"
    print("\n== Each site sends its public key, and the coordinator locks the secret")
    for site in sites:
        shutil.copy(
            PLAYGROUND / site / OUTBOX / "coordinator" / f"{site}.public_key.pprl",
            coordinator,
        )
    _pprl("coordinator", "secrets")
    for site in sites:
        shutil.copy(
            coordinator / OUTBOX / site / f"{site}.secret.pprl", PLAYGROUND / site
        )


def _demo_site_data(sites: list[str], *, messy: bool) -> None:
    """Make each site's patient file and score table, made messy if ``messy``."""
    coordinator = PLAYGROUND / "coordinator"
    for i, site in enumerate(sites):
        folder = PLAYGROUND / site
        export = coordinator / "synth" / site / "patients.csv"
        shutil.copy(export, folder / "patients.csv")
        # The clinical table that each site exports for the merge agent: one
        # score per person, keyed by the site's own person_id.
        patients = read_csv_frame(export)
        scores = patients[["person_id"]].copy()
        scores["score"] = [str(len(name)) for name in patients["name"]]
        (folder / "tables").mkdir()
        write_csv_frame(scores, folder / "tables" / "scores.csv")
        if messy:
            _messy(folder / "patients.csv", seed=i, windows_text=site == "site_b")
    if messy:
        print("\n(Each site's patient file is now a messy raw export.)")
        print("\n== What if site_a hashed its raw export without cleaning it?")
        raw = PLAYGROUND / "site_a" / "site_a.clean.pprl"
        shutil.copy(PLAYGROUND / "site_a" / "patients.csv", raw)
        _pprl("site_a", "hash")
        raw.unlink()


def _demo_sites(sites: list[str]) -> None:
    """Run each site's select, clean, hash and pack, and send its files on."""
    for site in sites:
        print(f"\n== {site} selects the study's rows of its tables")
        _pprl(site, "select")
        print(f"\n== {site} cleans and hashes its patient file, and packs its tables")
        _pprl(site, "clean")
        _pprl(site, "hash")
        _pprl(site, "pack")
        outbox = PLAYGROUND / site / OUTBOX
        shutil.copy(outbox / "linkage" / f"{site}.hashes.pprl", PLAYGROUND / "linkage")
        shutil.copy(outbox / "merge" / f"{site}.tables.pprl", PLAYGROUND / "merge")


def main(argv: list[str] | None = None) -> None:
    """Run the playground's command line."""
    parser = argparse.ArgumentParser(prog="python -m tests.playground")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list the usability cases")
    listing.add_argument("pprl_command", nargs="?", help="only this command's cases")
    trying = commands.add_parser("try", help="set up one usability case and run it")
    trying.add_argument("case", help="a case id from list, such as hash/12")
    demoing = commands.add_parser("demo", help="run every role on demo data")
    demoing.add_argument("--size", choices=SIZES, default="small")
    demoing.add_argument("--messy", action="store_true", help="raw exports to clean")
    demoing.add_argument("--keep", action="store_true", help="keep the workspaces")
    commands.add_parser("reset", help="empty the playground's workspaces")
    args = parser.parse_args(argv)
    if args.command == "list":
        list_cases(args.pprl_command)
    elif args.command == "try":
        try_case(args.case)
    elif args.command == "demo":
        demo(args.size, messy=args.messy, keep=args.keep)
    else:
        reset()


if __name__ == "__main__":
    # Importing pprl installs a handler that stops pprl quietly on Ctrl+C
    # while it starts. The playground handles Ctrl+C itself instead.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        main()
    except KeyboardInterrupt:  # Pressed between pprl's runs
        print("\nThe playground was stopped (Ctrl+C).", file=sys.stderr)
        sys.exit(INTERRUPTED)
