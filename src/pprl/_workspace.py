"""Manage the workspace: the folder that holds one role's files and its marker.

A workspace is a folder that holds the marker file, pprl-workspace.yml.
Every command except init and destroy runs in a workspace. The workspace is
always the current folder, never a parent of it. The
marker records whose workspace it is:

    # pprl workspace marker: pprl reads this file. Please leave it as it is, ...
    workspace: 7f3c2a9e-5b1d-4c8e-9a20-3e6f1d2b4c55
    project: 3b9e41c7-0d2a-4f6e-8a51-c2d7e9f01b34
    role: site
    site: site_a
    release: 3.0.0
    created: '2026-11-16T09:15:02-05:00'
    bundle_set: 5d0e8f61-2c3b-4a7d-9e15-b8a4c6f2d907

``bundle_set`` is the set id of the bundle the workspace was created with.
The coordinator's workspace has none. It lets pprl detect a bundle written
by a later run of ``pprl bundles``.

The marker is kept separate from the log. Users may edit the log before
they send it, but the marker is never sent.
"""

import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from . import _bundle
from . import _messages as msg
from ._io import YamlParseError, for_writing, load_yaml, validated_site
from ._shared import (
    RELEASE,
    Role,
    UserFacingError,
    is_other_release,
    is_text,
    log_name,
    now,
)

if TYPE_CHECKING:
    from typing import IO

MARKER_NAME = "pprl-workspace.yml"
MARKER_HEADER = (
    "# pprl workspace marker: pprl reads this file. Please leave it as it "
    "is, in this folder, and keep it out of anything you send.\n"
)


class WorkspaceError(UserFacingError):
    """Raised for a folder that pprl cannot work in."""


@dataclass(frozen=True)
class Marker:
    """The contents of a workspace's marker."""

    workspace: str
    project: str
    role: Role
    site: str | None
    release: str
    created: str
    bundle_set: str | None

    @property
    def owner(self) -> str:
        """Return a sentence saying whose workspace this is."""
        return msg.owner(self.role, self.site)

    @property
    def log_name(self) -> str:
        """Return the file name of the workspace's log, such as site_a.log."""
        return log_name(self.role, self.site)

    @property
    def bundle_name(self) -> str | None:
        """Return the file name of the workspace's bundle.

        Return None for the coordinator's workspace, which has no bundle.
        """
        if self.role == Role.COORDINATOR:
            return None
        return _bundle.bundle_name(self.role, self.site)


def create(
    folder: Path,
    *,
    project: str,
    role: str,
    site: str | None = None,
    bundle_set: str | None = None,
) -> Marker:
    """Make the existing folder ``folder`` a workspace by writing its marker.

    ``project`` is the project's id. ``site`` is the site's name, for a
    site's workspace only. ``bundle_set`` is the set id of the workspace's
    bundle; every role except the coordinator has one. Raise FileExistsError
    if the folder already has a marker.
    """
    if role not in Role:
        reason = f"unknown role {role!r}"
        raise ValueError(reason)
    if (role == Role.SITE) != (site is not None):
        reason = "a site's workspace, and only a site's, has a site"
        raise ValueError(reason)
    if site is not None:
        validated_site(site)
    if (role == Role.COORDINATOR) != (bundle_set is None):
        reason = "every workspace but the coordinator's has a bundle"
        raise ValueError(reason)
    marker = Marker(
        workspace=str(uuid.uuid4()),
        project=str(project),
        role=Role(role),
        site=site,
        release=RELEASE,
        created=now().isoformat(timespec="seconds"),
        bundle_set=bundle_set,
    )
    with for_writing(Path(folder) / MARKER_NAME) as handle:
        _write(handle, marker)
    return marker


def _write(handle: IO[str], marker: Marker) -> None:
    handle.write(MARKER_HEADER)
    # safe_dump only writes plain str values, so convert the role enum.
    contents = asdict(marker) | {"role": str(marker.role)}
    yaml.safe_dump(contents, handle, sort_keys=False, allow_unicode=True)


def _valid(contents: object) -> bool:
    """Tell whether a marker's contents have the form that ``create`` writes."""
    names = {f.name for f in fields(Marker)}
    if not isinstance(contents, dict) or set(contents) != names:
        return False
    site = contents["site"]
    role = contents["role"]
    if role not in Role or (role == Role.SITE) != (site is not None):
        return False
    if site is not None:
        try:
            validated_site(site)
        except ValueError:
            return False
    bundle_set = contents["bundle_set"]
    if (role == Role.COORDINATOR) != (bundle_set is None):
        return False
    if bundle_set is not None and not is_text(bundle_set):
        return False
    return all(
        is_text(value)
        for name, value in contents.items()
        if name not in ("site", "bundle_set")
    )


def is_workspace(folder: Path) -> bool:
    """Tell whether ``folder`` holds a marker, even one that pprl cannot read."""
    return (Path(folder) / MARKER_NAME).is_file()


def read(folder: Path) -> Marker:
    """Read the marker in ``folder``.

    Raise WorkspaceError if the folder has no marker, or has one that pprl
    cannot read. Parent folders are never searched.
    """
    if not is_workspace(folder):
        raise WorkspaceError(msg.NOT_A_WORKSPACE())
    try:
        contents = load_yaml(Path(folder) / MARKER_NAME)
    except OSError, UnicodeDecodeError, YamlParseError:
        raise WorkspaceError(msg.MARKER_DAMAGED()) from None
    if not _valid(contents) or not isinstance(contents, dict):
        raise WorkspaceError(msg.MARKER_DAMAGED())
    return Marker(**contents | {"role": Role(contents["role"])})


def verify(marker: Marker) -> None:
    """Check that pprl can work in the workspace whose marker is ``marker``.

    The workspace must have been made by this release. Raise WorkspaceError
    if it was not.
    """
    if is_other_release(marker.release):
        raise WorkspaceError(
            msg.WORKSPACE_OTHER_RELEASE(release=marker.release, version=RELEASE)
        )
