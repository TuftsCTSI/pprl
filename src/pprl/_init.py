"""Create a workspace with ``pprl init``.

The user creates a new, empty folder and puts the bundle they received in
it. Then they open a terminal there and run ``pprl init``. The command takes
no arguments: the workspace is always the current folder. ``pprl init``
does the following:

1. Find the bundle: the one pprl file in the folder whose header says it is
   a bundle. Other pprl files, such as hash files that arrived before the
   workspace was made, stay where they are. A pprl file whose header can't
   be read might be a damaged bundle, so it counts as one. If there are
   several bundles, or none and no project.yml, stop. Never look at the
   folder's files that aren't pprl files.
2. Check the bundle: it must be readable, complete, and meant for this
   release. If not, create nothing.
3. For a site, make its key pair. The private key stays in the workspace.
   The public key goes to outbox/coordinator/, to be sent to the coordinator.
4. Rename the bundle to its role's name if it has a different name. Write
   the workspace marker last.
5. Report each file it created, and record the same list in the log's first
   section.

The coordinator works differently. They write project.yml in a new folder,
and ``pprl init`` there makes the folder the coordinator's workspace for a
new project, with no bundle.

Running ``pprl init`` in an existing workspace lists the same files, each
with its status.
"""

import uuid
from typing import TYPE_CHECKING

from . import _bundle, _locking, _pprl_file, _wording, _workspace
from . import _messages as msg
from ._commands import FILES, provenance
from ._io import workspace
from ._project import PROJECT_FILE
from ._runs import in_workspace
from ._shared import Role, log_name
from ._ui import fail, finish_progress, info_block, note_user, ok_user

if TYPE_CHECKING:
    from pathlib import Path

    from ._workspace import Marker

# The workspace files that init creates.
_STATUS_KEYS = ("bundle", "private_key", "public_key", "marker", "log")


def _files(role: str, site: str | None, statuses: dict) -> None:
    """Describe a workspace's files, one line each, in the order init makes them.

    Each line gives the file's name, its status from ``statuses`` (keyed by
    _STATUS_KEYS), and what the file is.
    """
    names = {
        "marker": _workspace.MARKER_NAME,
        "log": log_name(role, site),
    }
    if role == Role.SITE:
        names = {
            "private_key": FILES["private_key"].named(site),
            "public_key": FILES["public_key"].written(site),
            **names,
        }
    if role != Role.COORDINATOR:
        names = {"bundle": _bundle.bundle_name(role, site), **names}
    info_block(
        [
            (name, msg.INIT_FILE(status=statuses[key], what=msg.INIT_WHAT[key]))
            for key, name in names.items()
        ]
    )


def _found_bundles(folder: Path) -> list[Path]:
    """Find the pprl files in ``folder`` that may be a bundle.

    These are the bundles, and any pprl file whose header can't be read. If
    there is exactly one, it is the bundle.
    """
    return sorted(
        path
        for path in folder.glob("*.pprl")
        if path.is_file() and _may_be_bundle(path)
    )


def _may_be_bundle(path: Path) -> bool:
    """Tell whether the file at ``path`` is a bundle, or may be a damaged one."""
    try:
        return _pprl_file.read_header(path)["kind"] == "bundle"
    except FileNotFoundError:  # The file was removed after it was found.
        return False
    except OSError, _pprl_file.PprlFileError:
        return True


def _create(
    command_line: str,
    role: str,
    project: str,
    *,
    bundle: dict | None = None,
    path: Path | None = None,
) -> int:
    """Make the current folder a workspace for ``role`` and return the exit code.

    The workspace belongs to the project with id ``project``. A site or agent
    workspace uses the settings in ``bundle``, read from the file at ``path``.
    The coordinator's workspace has neither.
    """
    folder = workspace()
    site = bundle.get("site") if bundle else None
    owner = msg.owner(role, site)
    name = bundle["project"]["name"] if bundle else None
    statuses = dict.fromkeys(_STATUS_KEYS, msg.INIT_STATUS["created"]())
    if role == Role.SITE:
        if bundle is None:  # Only the coordinator's workspace has no bundle.
            raise AssertionError
        try:
            _locking.write_key_pair(
                folder / FILES["private_key"].named(site),
                folder / FILES["public_key"].written(site),
                provenance(bundle),
            )
        except FileExistsError as e:
            return fail(str(e))
    if bundle:
        if path is None:  # A bundle always comes with the path it was read from.
            raise AssertionError
        bundle_name = _bundle.bundle_name(role, site)
        if path.name == bundle_name:
            statuses["bundle"] = msg.INIT_STATUS["kept"]()
        else:
            path.rename(folder / bundle_name)
            statuses["bundle"] = msg.INIT_STATUS["renamed"](
                name=_wording.supplied(path.name)
            )
    _workspace.create(
        folder,
        project=project,
        role=role,
        site=site,
        bundle_set=bundle["set"] if bundle else None,
    )

    def created(_marker: Marker, _: dict | None) -> int:
        finish_progress()
        if bundle:
            ok_user(msg.INIT_CREATED(owner=owner, project=name))
        else:
            ok_user(msg.INIT_COORDINATOR_CREATED(owner=owner))
        _files(role, site, statuses)
        return 0

    return in_workspace(command_line, created)


def init(command_line: str) -> int:
    """Run ``pprl init`` in a folder that is not yet a workspace.

    Make the folder a workspace from the bundle in it. If there is no bundle
    but there is a project.yml, make it the coordinator's workspace for a new
    project. Return the exit code.
    """
    folder = workspace()
    found = _found_bundles(folder)
    if not found and (folder / PROJECT_FILE).is_file():
        return _create(command_line, Role.COORDINATOR, str(uuid.uuid4()))
    if not found:
        return fail(msg.INIT_NO_BUNDLE())
    if len(found) > 1:
        names = _wording.listed(_wording.supplied(path.name) for path in found)
        return fail(msg.INIT_SEVERAL_BUNDLES(count=len(found), names=names))
    path = found[0]
    try:
        bundle = _bundle.read(path)
    except FileNotFoundError:  # The bundle was removed after it was found.
        return fail(msg.INIT_NO_BUNDLE())
    except _bundle.BundleError as e:
        return fail(
            msg.INIT_BUNDLE_UNUSABLE(name=_wording.supplied(path.name), reason=str(e))
        )
    return _create(
        command_line,
        bundle["role"],
        bundle["project"]["id"],
        bundle=bundle,
        path=path,
    )


def show_files(command_line: str) -> int:
    """Run ``pprl init`` in an existing workspace: list its files.

    Return the exit code.
    """

    def body(marker: Marker, bundle: dict | None) -> int:
        finish_progress()
        owner = marker.owner
        if bundle:
            note_user(msg.INIT_HERE(owner=owner, project=bundle["project"]["name"]))
        else:
            note_user(msg.INIT_COORDINATOR_HERE(owner=owner))
        here = dict.fromkeys(_STATUS_KEYS, msg.INIT_STATUS["here"]())
        _files(marker.role, marker.site, here)
        return 0

    return in_workspace(command_line, body)
