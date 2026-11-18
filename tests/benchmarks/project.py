"""Set up a project at a given size, for the benchmarks.

The project has every role's workspace, filled with made-up records. Each
command runs as a user runs it: the pprl command, in a process of its own.

A separate process gives each step its own peak memory, which pprl logs in
its "[INFO] finished" line. It also measures the time a user would wait,
including pprl's startup.
"""

import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pprl import _messages as msg
from pprl._io import for_appending, write_csv_frame
from pprl._pprl_file import file_name
from tests.benchmarks import records
from tests.commands import run_text
from tests.helpers import SITES, log_info_lines, make_workspace

if TYPE_CHECKING:
    from collections.abc import Iterable

    import pandas as pd

# Each step, in order: the workspace it runs in, and its command.
STEPS = {
    "select": ("site", ["select"]),
    "clean": ("site", ["clean"]),
    "hash": ("site", ["hash"]),
    "pack": ("site", ["pack"]),
    "link": ("linkage", ["link"]),
    "merge": ("merge", ["merge"]),
}
_PEAK = re.compile(r"peak memory (.+)$")


def _append(
    df: pd.DataFrame,
    path: Path,
    header: bool,  # noqa: FBT001 (write_tables calls it this way)
) -> None:
    """Append ``df``'s rows to the CSV file at ``path``.

    Write the header first if ``header`` is true (for the file's first rows).
    """
    with for_appending(path, newline="\n") as handle:
        df.to_csv(handle, header=header, index=False, lineterminator="\n")


def pprl_command() -> list:
    """Find the pprl command installed next to this Python."""
    found = shutil.which("pprl", path=str(Path(sys.executable).parent))
    return [found] if found else [sys.executable, "-m", "pprl.cli"]


@dataclass
class Measure:
    """How long a step took from start to finish, and its peak memory as pprl logged."""

    seconds: float
    memory: str


@dataclass
class Project:
    """Every role's workspace, for ``size`` records per site, in ``folder``.

    Each step runs once, the first time it is needed (see ``run``).
    """

    folder: Path
    size: int
    done: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Record that no step has run yet."""
        files = records.sites(self.size)
        for i, site in enumerate(SITES):
            workspace = self.folder / site
            make_workspace(workspace, site=site, tables=records.SITE_TABLES)
            write_csv_frame(files[site], workspace / "patients.csv")
            (workspace / "tables").mkdir()
            records.write_tables(files[site], workspace / "tables", _append, seed=i)
        make_workspace(self.folder / "linkage", role="linkage")
        make_workspace(self.folder / "merge", role="merge", tables=records.MERGE_TABLES)

    def prepare(self, step: str) -> None:
        """Run every step before ``step`` that has not run yet."""
        for earlier in list(STEPS)[: list(STEPS).index(step)]:
            self.run(earlier)

    def run(self, step: str) -> Measure:
        """Run ``step`` (see STEPS), after any earlier steps that haven't run yet.

        Return how long it took. For a step run at each site, return the
        slower site's time.
        """
        if step in self.done:
            return self.done[step]
        self.prepare(step)
        where, argv = STEPS[step]
        folders = (
            [self.folder / s for s in SITES]
            if where == "site"
            else [self.folder / where]
        )
        if step == "link":
            self._receive("linkage", [file_name("hashes", s) for s in SITES])
        if step == "merge":
            self._receive("merge", [file_name("tables", s) for s in SITES])
            linkages = (
                self.folder / "linkage" / "outbox" / "merge" / file_name("linkages")
            )
            shutil.copy(linkages, self.folder / "merge" / linkages.name)
        self.done[step] = max(
            (self._timed(folder, argv) for folder in folders),
            key=lambda m: m.seconds,
        )
        return self.done[step]

    def _receive(self, role: str, names: Iterable[str]) -> None:
        """Copy each site's ``names`` from its outbox into ``role``'s workspace.

        This stands in for a transfer between roles.
        """
        for site, name in zip(SITES, names, strict=True):
            shutil.copy(
                self.folder / site / "outbox" / role / name, self.folder / role / name
            )

    def _timed(self, folder: Path, argv: list[str]) -> Measure:
        started = time.perf_counter()
        result = run_text([*pprl_command(), *argv], cwd=folder, capture_output=True)
        seconds = time.perf_counter() - started
        assert result.returncode == 0, f"{argv} in {folder.name}:\n{result.stdout}"
        finished = log_info_lines(folder)[-1][-1]
        found = _PEAK.search(finished)
        return Measure(seconds, found.group(1) if found else msg.INFO_UNKNOWN)
