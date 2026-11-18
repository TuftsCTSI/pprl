"""Test the workspace.

Every command except check runs in a workspace, which is the current folder
holding the marker pprl-workspace.yml. The marker is read and checked
before every command. So a command never runs in a folder that is not a
workspace, or with another release of pprl.
"""

from importlib.metadata import version
from typing import TYPE_CHECKING

import pytest
import yaml

from pprl import _messages as msg
from pprl import _workspace
from pprl._io import read_text, write_text
from pprl._shared import Role
from pprl.cli import _COMMANDS
from tests.helpers import (
    TEST_PROJECT,
    files_in,
    has_log,
    log_sections,
    make_workspace,
    rewrite_marker,
    run_in,
    shows,
)
from tests.test_usability import style_problems

if TYPE_CHECKING:
    from pathlib import Path

# Each command, with the arguments it needs on the command line to run.
_ARGVS = [
    [command, "hashes.csv"] if command == "inspect" else [command]
    for command in _COMMANDS
    if command not in ("init", "destroy")  # These run outside a workspace.
]


@pytest.mark.reliability
class TestMarker:
    """A workspace's marker."""

    def test_round_trip(self, tmp_path: Path) -> None:
        """For every role, a marker reads back as written and passes its check."""
        for role in Role:
            folder = tmp_path / role
            written = make_workspace(folder, role=role)
            text = read_text(folder / _workspace.MARKER_NAME)
            assert text.startswith(_workspace.MARKER_HEADER), text
            assert _workspace.read(folder) == written
            _workspace.verify(_workspace.read(folder))
            assert written.project == TEST_PROJECT
            assert written.release == version("pprl")
            assert (written.site is not None) == (role == "site"), role

    def test_one_marker(self, tmp_path: Path) -> None:
        """A workspace is made only once. A second marker is refused."""
        make_workspace(tmp_path)
        before = read_text(tmp_path / _workspace.MARKER_NAME)
        with pytest.raises(FileExistsError):
            make_workspace(tmp_path)
        assert read_text(tmp_path / _workspace.MARKER_NAME) == before

    def test_damaged(self, tmp_path: Path) -> None:
        """A marker that differs from what pprl writes counts as damaged.

        This holds whatever is wrong with it.
        """
        make_workspace(tmp_path / "valid")
        valid = yaml.safe_load(read_text(tmp_path / "valid" / _workspace.MARKER_NAME))
        without_role = {k: v for k, v in valid.items() if k != "role"}
        damaged = {
            "not YAML": "workspace: [\n",
            "empty": "",
            "a list": "- site\n",
            "a missing setting": yaml.safe_dump(without_role),
            "an extra setting": yaml.safe_dump(valid | {"extra": "x"}),
            "an unknown role": yaml.safe_dump(valid | {"role": "host"}),
            "a site without a site": yaml.safe_dump(valid | {"site": None}),
            "a site that is not a name": yaml.safe_dump(valid | {"site": "a b"}),
            "a value that is not text": yaml.safe_dump(valid | {"release": 3}),
            "an empty value": yaml.safe_dump(valid | {"created": ""}),
            "not UTF-8": b"role: \xff\n",
        }
        for what, text in damaged.items():
            folder = tmp_path / what.replace(" ", "_")
            folder.mkdir()
            path = folder / _workspace.MARKER_NAME
            path.write_bytes(text if isinstance(text, bytes) else text.encode())
            with pytest.raises(_workspace.WorkspaceError) as problem:
                _workspace.read(folder)
            assert str(problem.value) == msg.MARKER_DAMAGED(), what

        for role in ("coordinator", "linkage", "merge"):
            folder = tmp_path / f"{role}_with_site"
            make_workspace(folder, role=role)
            rewrite_marker(folder, site="site_a")
            with pytest.raises(_workspace.WorkspaceError, match="damaged"):
                _workspace.read(folder)


@pytest.mark.security
class TestWorkspaceChecks:
    """The checks every command makes of its workspace."""

    def test_commands_need_a_workspace(self, tmp_path: Path) -> None:
        """Every command but init and destroy is refused outside a workspace.

        It is refused before it reads or writes anything.
        """
        write_text(tmp_path / "hashes.csv", "study_id,site,hash\n")
        before = files_in(tmp_path)
        for argv in _ARGVS:
            rc, output = run_in(tmp_path, argv)
            assert rc == 1, f"{argv} ran outside a workspace:\n{output}"
            assert shows(msg.NOT_A_WORKSPACE, output), output
            assert not style_problems(output, rejected=True), output
        assert files_in(tmp_path) == before, (
            "Nothing is written outside a workspace, not even a log"
        )

    def test_unknown_command_outside(self, tmp_path: Path) -> None:
        """A command pprl does not have is refused as unknown.

        This holds outside a workspace too, and nothing is written.
        """
        rc, output = run_in(tmp_path, ["nonexistent"])
        assert rc == 1, output
        assert shows(msg.UNKNOWN_COMMAND, output), output
        assert not shows(msg.NOT_A_WORKSPACE, output), output
        assert files_in(tmp_path) == [], "Nothing is written, not even a log"

    def test_no_bundle(self, tmp_path: Path) -> None:
        """A data command in the coordinator's workspace stops and names the setting.

        That workspace has no bundle. The command writes nothing but the log.
        """
        make_workspace(tmp_path, role="coordinator")
        before = files_in(tmp_path)
        rc, output = run_in(tmp_path, ["link"])
        assert rc == 1, output
        assert shows(
            msg.SETTING_NO_BUNDLE(command="pprl link", setting="sites"), output
        ), output
        assert not style_problems(output, rejected=True), output
        assert files_in(tmp_path) == sorted({*before, "coordinator.log"})

    def test_parents_not_searched(self, tmp_path: Path) -> None:
        """A folder inside a workspace is not a workspace."""
        make_workspace(tmp_path)
        (tmp_path / "inside").mkdir()
        rc, output = run_in(tmp_path / "inside", ["inspect", "hashes.csv"])
        assert rc == 1, output
        assert shows(msg.NOT_A_WORKSPACE, output), output
        assert files_in(tmp_path / "inside") == [], "Nothing is logged there"

    def test_other_release(self, tmp_path: Path) -> None:
        """A workspace made with another release of pprl is refused.

        The refusal is logged.
        """
        make_workspace(tmp_path)
        rewrite_marker(tmp_path, release="0.0.1")
        rc, output = run_in(tmp_path, ["inspect", "hashes.csv"])
        assert rc == 1, output
        assert shows(
            msg.WORKSPACE_OTHER_RELEASE(release="0.0.1", version=version("pprl")),
            output,
        ), output
        assert not style_problems(output, rejected=True), output
        [(_, _, body, _)] = log_sections(tmp_path)
        assert body == output, body

    def test_damaged_marker(self, tmp_path: Path) -> None:
        """A command in a workspace whose marker is damaged is refused.

        It is refused before a log is opened.
        """
        write_text(tmp_path / _workspace.MARKER_NAME, "workspace: [\n")
        rc, output = run_in(tmp_path, ["inspect", "hashes.csv"])
        assert rc == 1, output
        assert shows(msg.MARKER_DAMAGED, output), output
        assert not style_problems(output, rejected=True), output
        assert not has_log(tmp_path), "Nothing is logged"
