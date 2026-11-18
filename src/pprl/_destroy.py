"""Destroy a workspace with ``pprl destroy`` at the end of the project.

The user runs ``pprl destroy`` in the workspace, with no arguments. It does
the following:

1. Check that the current folder holds a marker, so that it never deletes
   any other folder. A damaged marker, or one from another release, is
   enough.
2. List every file in the folder with its size, and ask the user to type
   "yes". The list includes hidden and system files, whoever made them.
3. Delete every file it can. The marker is deleted last, once nothing else
   is left. The receipt is the record of this run, followed by the
   workspace's log. Like everything a role sends, it is written to the
   outbox: outbox/coordinator/<owner>.receipt_<date>.txt, such as
   outbox/coordinator/site_a.receipt_2026-11-16.txt. A second receipt on the
   same day ends in _2, then _3 and so on. If the marker cannot be read,
   the owner is unknown, and the receipt is named workspace.receipt_<date>.

The folder itself stays, because the user created it. It holds the receipt
and any files that could not be deleted, which the receipt lists. Running
``pprl destroy`` again deletes the rest and keeps the receipts.

The command writes no log, since it deletes the log. The receipt serves
as its log.
"""

import contextlib
import os
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from . import _messages as msg
from . import _wording, _workspace
from ._commands import FILES, ROLE_FILES, workspace_bundle
from ._io import read_text, workspace, write_text
from ._shared import OUTBOX, RELEASE, Role, is_other_release, now, owner_name
from ._ui import (
    add_work,
    advance,
    ask,
    fail,
    finish_progress,
    held,
    info_block,
    note_user,
    ok_user,
    progress,
    record,
    show,
)
from ._workspace import WorkspaceError

if TYPE_CHECKING:
    from datetime import datetime

    from ._workspace import Marker


def _listing(files: list[tuple[str, int]], known: set[str]) -> str:
    """List each (name, size) on its own line, with the sizes lined up.

    The numbers are right-aligned, and their units follow them. A name not in
    ``known`` belongs to the user (see ``_named``).
    """
    sizes = [_wording.size(size).split(" ") for _, size in files]
    name_width = max((len(name) for name, _ in files), default=0)
    number_width = max((len(number) for number, _ in sizes), default=0)
    return "\n".join(
        _named(name, known, f"{name:<{name_width}}")
        + f"  {number:>{number_width}} {unit}"
        for (name, _), (number, unit) in zip(files, sizes, strict=True)
    )


def _named(name: str, known: set[str], text: str | None = None) -> str:
    """Prepare ``text`` (the file name, by default) for use in a message.

    If pprl gave the file its name, the name is shown as it is. Otherwise the
    file is one the user put in the workspace, so its name counts as text
    the user supplied and is withheld from the log and the receipt.
    """
    text = name if text is None else text
    return text if name in known else _wording.supplied(text)


def known_names(marker: Marker | None, settings: dict | None) -> set:
    """List the names that pprl gives the files in a workspace, based on its role.

    The names include the marker, the log and the bundle, the role's inputs
    and outputs, and the files the role receives. An upload is listed under
    its own name and also under the name its recipient gives it. Each table's
    CSV file in a folder of tables is listed too. If the marker cannot be
    read, no role-specific names are included.
    """
    known = {_workspace.MARKER_NAME, "outbox"}
    if marker is None:
        return known
    known.add(marker.log_name)
    if marker.role == Role.COORDINATOR:
        return known | {"project.yml", "deployment.yml"}
    known.add(marker.bundle_name)
    settings = settings or {}
    files = ROLE_FILES[marker.role]
    site, sites = marker.site, settings.get("sites") or ()
    names = [
        *([FILES["public_key"].written(site)] if site else []),
        *(FILES[key].written(site) for key in files.uploads),
        *(
            name
            for key in (*files.reads, *files.writes, *files.uploads, *files.downloads)
            for name in FILES[key].each(site, sites)
        ),
    ]
    tables = settings.get("tables") or {}
    for name in names:
        if name.endswith("/"):
            known.add(name.removesuffix("/"))
            known |= {f"{name}{table}.csv" for table in tables}
        else:
            known.add(name)
    return known


def contents(folder: Path) -> list[tuple[str, int]]:
    """List every file and symbolic link under ``folder``, with its size.

    Links are never followed, so nothing outside the folder is listed or
    deleted. Each entry is (path relative to ``folder``, size), and the list
    is sorted. Receipts from earlier runs of pprl destroy are left out.
    """
    found = []
    for root, folders, files in os.walk(folder, followlinks=False):
        for name in [*files, *(f for f in folders if Path(root, f).is_symlink())]:
            path = Path(root) / name
            size = path.lstat().st_size if not path.is_symlink() else 0
            found.append((path.relative_to(folder).as_posix(), size))
    return sorted(entry for entry in found if not _is_receipt(entry[0]))


def _settings(folder: Path, marker: Marker | None) -> dict | None:
    """Return the workspace's bundle settings, or None if they cannot be read.

    A missing, damaged or replaced marker or bundle must not stop a workspace
    from being destroyed.
    """
    if marker is None or marker.role == Role.COORDINATOR:
        return {}
    try:
        return workspace_bundle(marker, folder)
    except WorkspaceError:
        return {}


def _notes(marker: Marker | None) -> list:
    """Note anything unusual about the workspace being destroyed, for the receipt.

    This is either a marker that cannot be read, or a workspace created with
    another release.
    """
    if marker is None:
        return [msg.DESTROY_NOTE_MARKER]
    if is_other_release(marker.release):
        return [msg.DESTROY_NOTE_RELEASE(release=marker.release, version=RELEASE)]
    return []


def destroy() -> int:
    """Run ``pprl destroy`` in a workspace and return the exit code.

    Permanently delete every file in the workspace. Leave the folder, with
    the receipt in it.
    """
    folder = workspace()
    if not _workspace.is_workspace(folder):
        return fail(msg.DESTROY_NOT_A_WORKSPACE())
    try:
        marker = _workspace.read(folder)
    except WorkspaceError:
        marker = None
    owner = marker.owner if marker else msg.DESTROY_UNKNOWN_OWNER
    files = contents(folder)
    known = known_names(marker, _settings(folder, marker))
    with held() as shown:
        note_user(
            msg.DESTROY_HOLDS(
                folder=_wording.supplied(folder.name),
                owner=owner,
                count=len(files),
                size=_wording.size(sum(size for _, size in files)),
            )
        )
        show(_listing(files, known))
        for note in _notes(marker):
            note_user(note)
        answer = ask(msg.DESTROY_PROMPT, answers=(msg.DESTROY_YES,))
    if answer is None or answer.lower() != msg.DESTROY_YES:
        return fail(msg.DESTROY_DECLINED())
    with progress("destroy", ((msg.STEP_DELETING, 1),)):
        return _delete(folder, marker, shown, files, known=known)


def _delete(
    folder: Path,
    marker: Marker | None,
    shown: list[str],
    files: list[tuple[str, int]],
    *,
    known: set[str],
) -> int:
    """Permanently delete every file in the workspace ``folder``.

    Files never go to a recycle bin. The marker is deleted last, once nothing
    else is left. Return the exit code.

    The receipt is written first, starting with the workspace's log,
    which is about to be deleted. It is completed at the end with this run's
    record (``shown`` and what follows), the files that could not be
    deleted, and then the log.
    """
    role = (
        msg.LOG_ROLES[marker.role](site=marker.site)
        if marker
        else msg.DESTROY_UNKNOWN_OWNER
    )
    receipt = _receipt_path(folder, marker, now())
    log = folder / marker.log_name if marker else None
    appendix = "\n" + (
        f"{msg.DESTROY_RECEIPT_LOG}\n{read_text(log)}"
        if log and log.is_file()
        else f"{msg.DESTROY_RECEIPT_NO_LOG}\n"
    )
    draft = [*shown, msg.DESTROY_RECEIPT_UNFINISHED]
    made = [p for p in (receipt.parent.parent, receipt.parent) if not p.exists()]
    try:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        write_text(receipt, record(role, "pprl destroy", draft, 1) + appendix)
    except OSError:
        for path in reversed(made):  # Leave the workspace as it was.
            with contextlib.suppress(OSError):
                path.rmdir()
        return fail(msg.DESTROY_RECEIPT_FAILED())
    left = _delete_files(folder, files)
    if not left:
        try:
            (folder / _workspace.MARKER_NAME).unlink()
        except OSError:
            left = [_workspace.MARKER_NAME]
    # The receipt shows what this run showed, wherever it runs, so no [DONE].
    if not left:
        finish_progress()
    with held() as rest:
        if left:
            fail(msg.DESTROY_INCOMPLETE(count=len(left)))
            show(_listing([entry for entry in files if entry[0] in left], known))
        else:
            _report(marker, files, receipt.relative_to(folder).as_posix())
    code = 1 if left else 0
    write_text(
        receipt,
        record(role, "pprl destroy", [*shown, *rest], code) + appendix,
        replace=True,
    )
    return code


def _delete_files(folder: Path, files: list[tuple[str, int]]) -> list:
    """Delete each of ``files`` in ``folder`` except the marker, then empty folders.

    Return the names of the files that could not be deleted.
    """
    left = []
    add_work(len(files))
    for name, _ in files:
        if name != _workspace.MARKER_NAME:
            try:
                _remove(folder / name)
            except OSError:
                left.append(name)
        advance()
    for root, folders, _ in os.walk(folder, topdown=False, followlinks=False):
        for name in folders:
            with contextlib.suppress(OSError):  # still holds an undeleted file
                Path(root, name).rmdir()
    return left


def _report(marker: Marker | None, files: list[tuple[str, int]], receipt: str) -> None:
    """Show what was deleted, the record that the receipt keeps, and its path."""
    ok_user(msg.DESTROYED(count=len(files)))
    labels = msg.DESTROY_RECORD
    rows = []
    if marker:
        rows += [
            (labels["workspace"], marker.workspace),
            (labels["project"], marker.project),
            (labels["created"], _wording.when(marker.created)),
        ]
    rows.append((labels["destroyed"], _wording.when(now())))
    info_block(rows)
    ok_user(msg.DESTROY_RECEIPT(name=receipt))


def _remove(path: Path) -> None:
    """Delete a file or a symbolic link, but never what a link points to.

    This works for links to files or folders, and for read-only files. On
    Windows, a file's read-only attribute is cleared first.
    """
    try:
        path.unlink()
    except IsADirectoryError, PermissionError:
        if path.is_symlink() or path.is_junction():
            path.rmdir()  # On Windows, a symbolic link to a folder
            return
        if not path.is_file():
            raise
        path.chmod(stat.S_IWRITE)
        path.unlink()


# Where receipts are written, in the workspace: they are sent to the coordinator.
RECEIPTS = f"{OUTBOX}/coordinator"
# A receipt's path in the workspace, whoever's it is (see receipt_name).
_RECEIPT = re.compile(
    rf"{re.escape(RECEIPTS)}/[A-Za-z0-9_]+\.receipt_\d{{4}}-\d{{2}}-\d{{2}}(_\d+)?\.txt"
)


def receipt_name(owner: str, date: str, n: int) -> str:
    """Return the name of ``owner``'s receipt on ``date``.

    For the ``n``th receipt of the same day, add _2, _3... to the name.
    """
    return msg.DESTROY_RECEIPT_NAME(
        owner=owner, date=date, suffix="" if n == 1 else f"_{n}"
    )


def _is_receipt(name: str) -> bool:
    """Tell whether ``name``, a path in a workspace, is a receipt from pprl destroy."""
    return _RECEIPT.fullmatch(name) is not None


def _receipt_path(folder: Path, marker: Marker | None, now: datetime) -> Path:
    """Return the receipt's path, in the outbox, named for the owner and date.

    The owner is unknown if the marker cannot be read. Add _2, _3... if a
    file with that name is already there.
    """
    owner = owner_name(marker.role, marker.site) if marker else msg.DESTROY_NO_OWNER
    for n in range(1, 1000):
        path = folder / RECEIPTS / receipt_name(owner, now.strftime("%Y-%m-%d"), n)
        if not path.exists():
            return path
    raise FileExistsError(path)
