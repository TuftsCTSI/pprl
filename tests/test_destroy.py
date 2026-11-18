"""Test destroying a workspace with pprl destroy.

pprl destroy runs only in a workspace. It deletes every file in the
workspace that it can. It leaves the folder, holding the receipt and
anything it could not delete, which the receipt lists. It first lists what
is there, and deletes nothing unless the user types "yes". Any folder with
a marker can be destroyed, even one whose marker or bundle is damaged, or
one from another release.
"""

import contextlib
import os
import re
from pathlib import Path
from typing import Never

import pytest
import yaml

from pprl import _destroy, _wording
from pprl import _messages as msg
from pprl._io import read_text, write_text
from pprl._shared import now
from tests.commands import DESTROY_YES
from tests.helpers import (
    Logged,
    files_in,
    log_of,
    log_sections,
    make_workspace,
    rewrite_marker,
    run_in,
    shows,
)
from tests.test_usability import style_problems

pytestmark = pytest.mark.security


@pytest.fixture
def deleted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Instead of deleting, record the workspaces that would be deleted."""
    calls = []

    def record(
        folder: Path, *_: object, **__: object
    ) -> int:  # Replaces _destroy._delete
        calls.append(folder.name)
        return 0

    monkeypatch.setattr(_destroy, "_delete", record)
    return calls


def _destroy_in(folder: Path, answers: str) -> tuple[int, str]:
    """Run pprl destroy in ``folder``, typing ``answers``."""
    return run_in(folder, ["destroy"], answers)


def _receipt(folder: Path) -> Path:
    """Find the one receipt in ``folder``."""
    [receipt] = folder.glob("outbox/coordinator/*.receipt_*.txt")
    return receipt


def _receipts(folder: Path) -> list[Path]:
    """List every receipt in ``folder``, in order."""
    return sorted(folder.glob("outbox/coordinator/*.receipt_*.txt"))


def _relative(receipt: Path, folder: Path) -> str:
    """Return the receipt's path in ``folder``, as files_in lists it."""
    return receipt.relative_to(folder).as_posix()


def _study(tmp_path: Path, role: str = "site") -> Path:
    """Make a workspace with files in it, as a project would leave it."""
    study = tmp_path / "study"
    make_workspace(study, role=role)
    write_text(study / "patients.csv", "person_id,name\n1,A\n")
    (study / "tables").mkdir()
    write_text(study / "tables" / "scores.csv", "person_id,score\n1,7\n")
    run_in(study, ["inspect", "patients.csv"])  # Creates a log, as any workspace has
    return study


class TestRefusing:
    """When pprl destroy refuses to run."""

    def test_not_a_workspace(self, tmp_path: Path, deleted: list[str]) -> None:
        """A folder that is not a workspace is left untouched, before any prompt."""
        (tmp_path / "photos").mkdir()
        (tmp_path / "photos" / "cat.jpg").write_bytes(b"cat")
        rc, output = _destroy_in(tmp_path / "photos", DESTROY_YES)
        assert rc == 1, output
        assert shows(msg.DESTROY_NOT_A_WORKSPACE, output), output
        assert msg.DESTROY_PROMPT not in output
        assert not style_problems(output, rejected=True), output
        assert files_in(tmp_path / "photos") == ["cat.jpg"]
        assert deleted == []

    def test_inside(self, tmp_path: Path, deleted: list[str]) -> None:
        """Pprl destroy is refused in a folder inside a workspace.

        The workspace must be the folder it runs in.
        """
        study = _study(tmp_path)
        rc, output = _destroy_in(study / "tables", DESTROY_YES)
        assert rc == 1, output
        assert shows(msg.DESTROY_NOT_A_WORKSPACE, output), output
        assert deleted == []

    def test_arguments(self, tmp_path: Path, deleted: list[str]) -> None:
        """Pprl destroy takes no arguments: the workspace is the current folder."""
        _study(tmp_path)
        rc, output = run_in(tmp_path, ["destroy", "study"])
        assert rc == 1, output
        assert shows(msg.EXTRA_ARGUMENTS, output), output
        assert deleted == []

    @pytest.mark.parametrize("typed", ["no", "", "y", "study"])
    def test_declined(self, tmp_path: Path, typed: str, deleted: list[str]) -> None:
        """Any answer other than "yes" (in any case) deletes nothing."""
        _study(tmp_path)
        rc, output = _destroy_in(tmp_path / "study", f"{typed}\n")
        assert rc == 1, output
        assert shows(msg.DESTROY_DECLINED, output), output
        assert output.count(msg.DESTROY_PROMPT) == 1, "It asks once"
        assert not style_problems(output, rejected=True), output
        assert deleted == []

    def test_yes(self, tmp_path: Path, deleted: list[str]) -> None:
        """Typing "yes" in any mix of upper and lower case deletes the workspace."""
        _study(tmp_path)
        rc, output = _destroy_in(tmp_path / "study", "YES\n")
        assert rc == 0, output
        assert deleted == ["study"], output

    def test_no_answer(self, tmp_path: Path, deleted: list[str]) -> None:
        """No answer at all (the input ends) does not count as yes."""
        _study(tmp_path)
        rc, output = _destroy_in(tmp_path / "study", "")
        assert rc == 1, output
        assert shows(msg.DESTROY_DECLINED, output), output
        assert deleted == []

    @pytest.mark.usefixtures("deleted")
    def test_not_logged(self, tmp_path: Path) -> None:
        """Pprl destroy adds nothing to the workspace's log.

        Its receipt is its record.
        """
        study = _study(tmp_path)
        before = log_sections(study)
        _destroy_in(tmp_path / "study", DESTROY_YES)
        assert log_sections(study) == before


class TestListing:
    """What pprl destroy lists before it deletes."""

    def test_files(self, tmp_path: Path, deleted: list[str]) -> None:
        """Every file is listed with its size, including files in subfolders."""
        study = _study(tmp_path)
        (study / "big.csv").write_bytes(b"x" * 3_000)
        rc, output = _destroy_in(tmp_path / "study", DESTROY_YES)
        assert rc == 0, output
        assert deleted == ["study"], output
        names = files_in(study)
        files = [n for n in names if (study / n).is_file()]
        assert f"holds {_wording.counted(len(files), 'file(s)')}" in output, output
        listed = output.splitlines()
        for name in files:
            assert any(
                re.fullmatch(rf" {{7}}{re.escape(name)} +\d[\d.]* \w+", line)
                for line in listed
            ), name
        assert any(re.fullmatch(r" {7}big\.csv +2\.9 KB", line) for line in listed), (
            output
        )

    @pytest.mark.usefixtures("deleted")
    def test_link_not_followed(self, tmp_path: Path) -> None:
        """A link to a folder outside the workspace is listed as a link.

        What it points to is neither listed nor counted.
        """
        study = _study(tmp_path)
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        write_text(outside / "keep.csv", "keep\n")
        try:
            (study / "link").symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("This system does not allow symbolic links")
        listed = dict(_destroy.contents(study))
        assert "link" in listed
        assert listed["link"] == 0
        assert not any(name.startswith("link/") for name in listed)

    def test_any_workspace(self, tmp_path: Path, deleted: list[str]) -> None:
        """Any workspace can be destroyed, even one with a bad bundle or old release.

        A workspace from another release, or whose bundle is missing or
        changed, can still be destroyed. pprl says when the release differs,
        so the receipt records it.
        """
        study = _study(tmp_path)
        rewrite_marker(study, release="0.0.1")
        write_text(study / "site_a.pprl", "changed\n", replace=True)
        rc, output = _destroy_in(study, DESTROY_YES)
        assert rc == 0, output
        assert deleted == ["study"], output
        assert "This workspace was created with pprl 0.0.1" in output, output
        (study / "site_a.pprl").unlink()
        rc, output = _destroy_in(study, DESTROY_YES)
        assert rc == 0, output
        assert deleted == ["study", "study"], output
        assert log_of(study).exists(), "Nothing was deleted yet"

    def test_damaged_marker(self, tmp_path: Path, deleted: list[str]) -> None:
        """A folder whose marker cannot be read can be destroyed too.

        Its files are listed, and pprl says it doesn't know whose workspace it is.
        """
        study = _study(tmp_path)
        write_text(study / "pprl-workspace.yml", "workspace: [\n", replace=True)
        rc, output = _destroy_in(study, DESTROY_YES)
        assert rc == 0, output
        assert deleted == ["study"], output
        assert msg.DESTROY_NOTE_MARKER in output, output
        assert msg.DESTROY_UNKNOWN_OWNER in output, output


class TestDeleting:
    """What pprl destroy deletes, and its receipt."""

    SECRET_VALUE = "PATIENT_NAME_SHOULD_NOT_APPEAR"  # noqa: S105 (a canary value)

    def _full(self, tmp_path: Path) -> tuple[Path, Path]:
        """Make a workspace with patient data, folders, and a link to outside.

        The link is a symbolic link to a folder outside the workspace.
        """
        study = _study(tmp_path)
        write_text(
            study / "patients.csv",
            f"person_id,name\n1,{self.SECRET_VALUE}\n",
            replace=True,
        )
        (study / "merged" / "deep").mkdir(parents=True)
        write_text(study / "merged" / "deep" / "table.csv", "a\n1\n")
        write_text(study / ".DS_Store", "system\n")
        write_text(study / "my notes.txt", "notes\n")
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        write_text(outside / "keep.csv", "keep\n")
        # If symbolic links aren't allowed here, the rest of the test still applies.
        with contextlib.suppress(OSError):
            (study / "link").symlink_to(outside, target_is_directory=True)
        return study, outside

    def test_receipt_withholds_user_text(self, tmp_path: Path) -> None:
        """The receipt, which is sent to others, withholds what the user supplied.

        It withholds the workspace folder's name and the names of files the
        user put in the workspace. The terminal shows them. pprl's own files
        are named in both.
        """
        canary = "Canary_Q7"
        study = _study(tmp_path)
        study = study.rename(tmp_path / canary)
        write_text(study / f"{canary}.txt", "notes\n")
        rc, output = _destroy_in(tmp_path / canary, DESTROY_YES)
        assert rc == 0, output
        assert canary in output, "The terminal shows the names"
        text = read_text(_receipt(study))
        assert canary not in text, text
        assert _wording.WITHHELD in text, text
        assert "patients.csv" in text, text

    def test_deletes(self, tmp_path: Path) -> None:
        """Every file in the workspace is deleted, but not what a link points to.

        Hidden files and files the user made are deleted too. What a symbolic
        link points to is not. The folder is left with the receipt in it. The
        receipt records the run, then holds the workspace's log, and
        contains no patient data.
        """
        study, outside = self._full(tmp_path)
        marker = yaml.safe_load(read_text(study / "pprl-workspace.yml"))
        log = read_text(log_of(study))
        rc, output = _destroy_in(tmp_path / "study", DESTROY_YES)
        assert rc == 0, output
        assert shows(msg.DESTROYED, output), output
        assert not style_problems(output, rejected=False), output
        receipt = _receipt(study)
        assert files_in(study) == [
            "outbox",
            "outbox/coordinator",
            _relative(receipt, study),
        ], "Only the receipt is left"
        assert files_in(outside) == ["keep.csv"], "A link's target is left alone"
        text = read_text(receipt)
        run, appendix = text.split("----- exit code 0 -----\n", 1)
        lines = run.splitlines()
        assert lines[0].startswith("===== ")
        assert lines[0].endswith(" · site_a · pprl destroy =====")
        body = Logged("\n".join(lines[2:]) + "\n")
        assert body == output, "The receipt is the run, with folder names withheld"
        assert appendix == f"\n{msg.DESTROY_RECEIPT_LOG}\n{log}", "Then the log"
        assert marker["workspace"] in text
        assert marker["project"] in text
        assert self.SECRET_VALUE not in text, "No patient data"

    def test_marker_last(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The marker is the last file deleted."""
        self._full(tmp_path)
        order = []
        real = Path.unlink

        def unlink(path: Path, *, missing_ok: bool = False) -> None:
            order.append(path.name)
            return real(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", unlink)
        rc, output = _destroy_in(tmp_path / "study", DESTROY_YES)
        assert rc == 0, output
        assert order[-1] == "pprl-workspace.yml"
        assert order.count(order[-1]) == 1

    def test_partly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A file that can't be deleted is left and listed, and so is the marker.

        Everything else is deleted, and the receipt says so. Running pprl
        destroy again deletes the rest and keeps the first receipt.
        """
        study, _ = self._full(tmp_path)
        real = _destroy._remove

        def in_use(path: Path) -> None:
            if path.name == "patients.csv":
                raise PermissionError(13, "in use")
            real(path)

        monkeypatch.setattr(_destroy, "_remove", in_use)
        rc, output = _destroy_in(tmp_path / "study", DESTROY_YES)
        assert rc == 1, output
        assert shows(msg.DESTROY_INCOMPLETE, output), output
        assert re.search(r"^ {7}patients\.csv +\d", output, re.MULTILINE), output
        assert not style_problems(output, rejected=True), output
        first = _receipt(study)
        left = [name for name in files_in(study) if (study / name).is_file()]
        assert left == [
            _relative(first, study),
            "patients.csv",
            "pprl-workspace.yml",
        ], left
        text = read_text(first)
        assert "exit code 1" in text, text
        assert "patients.csv" in text, text
        assert msg.DESTROY_RECEIPT_LOG in text, "The receipt holds the log"
        monkeypatch.setattr(_destroy, "_remove", real)
        rc, output = _destroy_in(tmp_path / "study", DESTROY_YES)
        assert rc == 0, output
        assert first.name not in output, "The first receipt is not listed"
        second = first.with_name(first.name.replace(".txt", "_2.txt"))
        assert _receipts(study) == sorted([first, second]), files_in(study)

    def test_receipt_names(self, tmp_path: Path) -> None:
        """A receipt already in the folder is never overwritten or deleted.

        The new receipt gets a number.
        """
        study = _study(tmp_path)
        rc, output = _destroy_in(study, DESTROY_YES)
        assert rc == 0, output
        earlier = _receipt(study)
        make_workspace(study)
        rc, output = _destroy_in(study, DESTROY_YES)
        assert rc == 0, output
        second = earlier.with_name(earlier.name.replace(".txt", "_2.txt"))
        assert _receipts(study) == sorted([earlier, second]), files_in(study)
        assert earlier.name == f"site_a.receipt_{now():%Y-%m-%d}.txt", earlier.name


def _unchanged(study: Path, before: list[str]) -> bool:
    """Tell whether nothing was deleted: the files are unchanged, with no receipt."""
    return files_in(study) == before


_POSIX_USER = os.name == "posix" and os.geteuid() != 0  # Permissions apply (not root)


class TestWhatItCan:
    """When pprl destroy can delete only some files."""

    @pytest.mark.skipif(not _POSIX_USER, reason="needs POSIX permissions")
    def test_blocked(self, tmp_path: Path) -> None:
        """Files in a folder the user may not change are left in place and listed.

        Everything else is deleted.
        """
        study = _study(tmp_path)
        (study / "tables").chmod(0o555)
        try:
            rc, output = _destroy_in(tmp_path / "study", DESTROY_YES)
        finally:
            (study / "tables").chmod(0o755)
        assert rc == 1, output
        assert shows(msg.DESTROY_INCOMPLETE, output), output
        assert "tables/scores.csv" in output, output
        left = [name for name in files_in(study) if (study / name).is_file()]
        receipt = _relative(_receipt(study), study)
        assert left == [receipt, "pprl-workspace.yml", "tables/scores.csv"], left

    def test_receipt_unwritable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the receipt cannot be written, nothing is deleted."""
        study = _study(tmp_path)
        before = files_in(study)

        def full(*_: object, **__: object) -> Never:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(_destroy, "write_text", full)
        rc, output = _destroy_in(study, DESTROY_YES)
        assert rc == 1, output
        assert shows(msg.DESTROY_RECEIPT_FAILED, output), output
        assert _unchanged(study, before), files_in(study)

    def test_read_only_file(self, tmp_path: Path) -> None:
        """A read-only file is deleted like any other."""
        study = _study(tmp_path)
        (study / "patients.csv").chmod(0o444)
        rc, output = _destroy_in(tmp_path / "study", DESTROY_YES)
        assert rc == 0, output
        assert files_in(study) == [
            "outbox",
            "outbox/coordinator",
            _relative(_receipt(study), study),
        ]
