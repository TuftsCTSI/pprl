"""Test creating a workspace with pprl init.

pprl init runs in a new folder that holds the bundle. It checks the bundle,
makes the folder the workspace, and reports each file it created. It asks
no questions. Running pprl init in an existing workspace lists the same
files.
"""

import re
import shutil
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from pprl import _bundle, _pprl_file, _workspace
from pprl import _messages as msg
from pprl._io import write_text
from pprl._shared import HASH_COLUMNS, Member
from tests.helpers import (
    PROJECT_SETTINGS,
    coordinator_workspace,
    has_log,
    log_sections,
    rewrite_header,
    run_in,
    shows,
)
from tests.test_usability import style_problems

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

pytestmark = pytest.mark.reliability


def _bundles(folder: Path) -> Path:
    """Write every bundle of a new project with pprl bundles.

    The bundles are written in the coordinator's workspace ``folder``.
    """
    coordinator_workspace(folder)
    return folder


@pytest.fixture
def downloads(tmp_path: Path) -> Path:
    """Make a new folder for the workspace, holding site_a's bundle."""
    coordinator = _bundles(tmp_path / "coordinator")
    folder = tmp_path / "study"
    folder.mkdir()
    shutil.copy(coordinator / "outbox" / "site_a" / "site_a.pprl", folder)
    return folder


def _init(folder: Path, argv: Sequence[str] = ()) -> tuple[int, str]:
    """Run pprl init in ``folder``."""
    return run_in(folder, ["init", *argv])


class TestCreating:
    """Pprl init creating a workspace."""

    def test_creates(self, downloads: Path) -> None:
        """Init makes the folder a workspace, and reports what it created.

        It keeps the bundle, makes a site's keys, and asks nothing. The log's
        first section holds everything that was shown.
        """
        rc, output = _init(downloads)
        assert rc == 0, output
        assert sorted(p.name for p in downloads.iterdir()) == [
            "outbox",
            "pprl-workspace.yml",
            "site_a.log",
            "site_a.pprl",
            "site_a.private_key.pprl",
        ]
        bundle = _bundle.read(downloads / "site_a.pprl")
        marker = _workspace.read(downloads)
        assert (marker.role, marker.site) == ("site", "site_a")
        assert marker.project == bundle["project"]["id"]
        assert marker.bundle_set == bundle["set"]
        assert shows(msg.INIT_CREATED, output), output
        for name, status in (
            ("site_a.pprl", "kept"),
            ("pprl-workspace.yml", "created"),
            ("site_a.log", "created"),
        ):
            assert _file_line(name, status).search(output), output
        assert not style_problems(output, rejected=False), output
        [(header, _, body, footer)] = log_sections(downloads)
        assert header.endswith(" · pprl init =====")
        assert body == output
        assert footer == "----- exit code 0 -----"

    def test_other_pprl_files(self, downloads: Path) -> None:
        """Init finds the bundle among other pprl files, and leaves them be.

        The linkage agent, for example, may receive hash files before it
        makes its workspace.
        """
        bundle = _pprl_file.read_header(downloads / "site_a.pprl")
        other = downloads / "site_b.hashes.pprl"
        _pprl_file.write(
            other,
            "hashes",
            {Member.HASHES: pd.DataFrame(columns=list(HASH_COLUMNS))},
            provenance={"project": bundle["project"], "set": bundle["set"]}
            | {"site": "site_b"},
        )
        before = other.read_bytes()
        rc, output = _init(downloads)
        assert rc == 0, output
        assert _file_line("site_a.pprl", "kept").search(output), output
        assert other.read_bytes() == before, "The other pprl file stays as it was"

    def test_renamed(self, downloads: Path) -> None:
        """A bundle saved under another name is renamed to its proper name.

        A browser does this, for example, with a second download.
        """
        (downloads / "site_a.pprl").rename(downloads / "site_a (1).pprl")
        rc, output = _init(downloads)
        assert rc == 0, output
        assert (downloads / "site_a.pprl").is_file(), output
        assert _file_line("site_a.pprl", "renamed from site_a (1).pprl").search(output)

    def test_log_withholds_user_text(self, tmp_path: Path) -> None:
        """A bundle name that the user chose is shown, but withheld from the log.

        The name belongs to the user.
        """
        canary = "Canary_Q7"
        coordinator = _bundles(tmp_path / "coordinator")
        folder = tmp_path / "study"
        folder.mkdir()
        shutil.copy(
            coordinator / "outbox" / "site_a" / "site_a.pprl", folder / f"{canary}.pprl"
        )
        rc, output = _init(folder)
        assert rc == 0, output
        assert canary in output, output
        [(_, _, body, _)] = log_sections(folder)
        assert canary not in body, body

    @pytest.mark.parametrize(
        ("role", "file"),
        [
            ("site", "site_b.pprl"),
            ("linkage", "linkage.pprl"),
            ("merge", "merge.pprl"),
        ],
    )
    def test_each_role(self, tmp_path: Path, role: str, file: str) -> None:
        """Init makes each role's workspace, and says whose it is.

        Running pprl on its own there then lists every command.
        """
        coordinator = _bundles(tmp_path / "coordinator")
        folder = tmp_path / "w"
        folder.mkdir()
        shutil.copy(coordinator / "outbox" / file.removesuffix(".pprl") / file, folder)
        rc, output = _init(folder)
        assert rc == 0, output
        owner = msg.owner(role, "site_b")
        project = PROJECT_SETTINGS["name"]
        assert shows(msg.INIT_CREATED(owner=owner, project=project), output), output
        rc, output = run_in(folder, [])
        assert shows(msg.HELP_GROUPS[-1][0], output), output

    def test_other_files(self, downloads: Path) -> None:
        """Init ignores the folder's other files, such as files the system adds.

        Its warning that every file in the folder is deleted at the end is
        enough.
        """
        write_text(downloads / "desktop.ini", "[.ShellClassInfo]\n")
        rc, output = _init(downloads)
        assert rc == 0, output
        assert _workspace.read(downloads).role == "site"


def _file_line(name: str, status: str) -> re.Pattern[str]:
    """Match the start of init's line about the file ``name``."""
    return re.compile(rf"{re.escape(name)} +{re.escape(status)}: ")


class TestRefusing:
    """When pprl init refuses to make a workspace."""

    def _nothing_created(
        self, folder: Path, bundle: str | None = "site_a.pprl"
    ) -> None:
        assert not (folder / "pprl-workspace.yml").exists(), "Nothing is created"
        assert not has_log(folder), "Nothing is logged"
        if bundle:
            assert (folder / bundle).exists(), "The bundle stays as it was"

    def test_no_bundle(self, downloads: Path) -> None:
        """Init stops if there is no bundle in the folder."""
        (downloads / "site_a.pprl").unlink()
        rc, output = _init(downloads)
        assert rc == 1, output
        assert shows(msg.INIT_NO_BUNDLE, output), output
        self._nothing_created(downloads, bundle=None)

    def test_several(self, tmp_path: Path, downloads: Path) -> None:
        """Init stops if there is more than one bundle in the folder."""
        shutil.copy(
            tmp_path / "coordinator" / "outbox" / "site_b" / "site_b.pprl", downloads
        )
        rc, output = _init(downloads)
        assert rc == 1, output
        assert shows(msg.INIT_SEVERAL_BUNDLES, output), output
        assert "site_a.pprl" in output, output
        assert "site_b.pprl" in output, output
        self._nothing_created(downloads)

    def test_bundle_problems(self, downloads: Path) -> None:
        """A bad bundle stops init before anything is created.

        A bad bundle is one that is not a bundle, is damaged, or is for
        another release.
        """
        path = downloads / "site_a.pprl"
        valid = path.read_bytes()
        rewrite_header(path, release="0.0.1")
        other_release = path.read_bytes()
        for data, says in (
            (b"role: [\n", msg.BUNDLE_REASONS["not a bundle"]()),
            (valid[:-20], msg.BUNDLE_REASONS["not a bundle"]()),
            (other_release, "it is for pprl 0.0.1"),
        ):
            path.write_bytes(data)
            rc, output = _init(downloads)
            assert rc == 1, output
            assert shows(says, output), output
            assert not style_problems(output, rejected=True), output
            self._nothing_created(downloads)

    def test_arguments(self, downloads: Path) -> None:
        """Init takes no arguments, because the workspace is the current folder."""
        rc, output = _init(downloads, ["../s"])
        assert rc == 1, output
        assert shows(msg.EXTRA_ARGUMENTS, output), output
        self._nothing_created(downloads)


class TestAfterwards:
    """A workspace after it has been made."""

    def test_again(self, downloads: Path) -> None:
        """Run again, init lists the same files, each marked as already there."""
        rc, output = _init(downloads)
        assert rc == 0, output
        rc, output = _init(downloads)
        assert rc == 0, output
        assert shows(msg.INIT_HERE, output), output
        for name in ("site_a.pprl", "pprl-workspace.yml", "site_a.log"):
            assert _file_line(name, "already here").search(output), output

    def test_moved(self, downloads: Path) -> None:
        """A workspace moved or copied elsewhere works there as before."""
        rc, output = _init(downloads)
        assert rc == 0, output
        moved = downloads.parent / "moved"
        shutil.copytree(downloads, moved)
        rc, output = run_in(moved, ["inspect", "site_a.pprl"])
        assert rc == 0, output

    def test_replaced_bundle(self, tmp_path: Path, downloads: Path) -> None:
        """A workspace whose bundle was replaced by another is refused.

        The replacement is for the same project, role and site, but from a
        later run of pprl bundles.
        """
        rc, output = _init(downloads)
        assert rc == 0, output
        coordinator = tmp_path / "coordinator"
        for path in (coordinator / "outbox").glob("*/*.pprl"):
            path.unlink()
        rc, output = run_in(coordinator, ["bundles"])
        assert rc == 0, output
        shutil.copy(
            coordinator / "outbox" / "site_a" / "site_a.pprl", downloads / "site_a.pprl"
        )
        rc, output = run_in(downloads, ["hash"])
        assert rc == 1, output
        assert shows(msg.BUNDLE_UNUSABLE, output)
        assert msg.BUNDLE_REASONS["replaced"]() in output
