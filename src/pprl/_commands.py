"""Define each role's commands, their fixed file names, and the bundle they read.

A command takes nothing from the user except the workspace it runs in. Its
settings come from the workspace's bundle. Its files have fixed names, so
each role's guide can name them exactly. These rules hold for every file:

1. Generated files never change names. A file is saved under the same name
   by the role that writes it and by every role that receives it.
2. Everything a role must send is written to outbox/<recipient>/, where the
   recipient is a role (coordinator, linkage, merge or host) or a site. A log
   file may be shared if required for troubleshooting. All other files must
   never be shared.
3. No generated file or folder name has a space. Names use underscores and
   dots.
4. Commands take no file argument. They read fixed names in the workspace.

Every other file, including inputs and received files, is in the workspace
folder itself. For site_a:

    site     init   site_a.pprl
                        -> site_a.private_key.pprl,
                           outbox/coordinator/site_a.public_key.pprl
             select tables/<table>.csv -> site_a.selected.pprl
             clean  patients.csv, site_a.selected.pprl
                        -> site_a.clean.pprl, site_a.set_aside.csv
             hash   site_a.clean.pprl, site_a.private_key.pprl,
                    site_a.secret.pprl
                        -> outbox/linkage/site_a.hashes.pprl
             pack   site_a.selected.pprl, site_a.clean.pprl
                        -> outbox/merge/site_a.tables.pprl
    linkage  link   site_X.hashes.pprl for every site
                        -> outbox/merge/linkages.pprl,
                           outbox/coordinator/linkage_report.pprl
    merge    merge  linkages.pprl, site_X.tables.pprl for every site
                        -> outbox/host/merged/

The coordinator writes the rest: each recipient's bundle and guide, in
outbox/<recipient>/, and each site's secret, in outbox/<site>/. It saves the
linkage report in its workspace, where pprl review reads it.

``FILES`` names each file once, along with the role it is sent to. Each
command names the files it reads and writes. Each role's files
(``ROLE_FILES``) follow from its commands' files. A command's help, each
role's guide, and what init and destroy say about a workspace's files all
take their file names from these definitions.

Every pprl file a command reads must belong to this workspace's project
and bundle set. Any command can be started in any workspace. If the
bundle lacks a setting the command needs, the command stops and names the
setting.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import _bundle, _clean, _hash, _link, _merge, _pack, _select, _wording
from . import _messages as msg
from ._config import run_command
from ._pprl_file import file_name
from ._shared import OUTBOX, Role, outbox
from ._ui import fail, info_log, progress
from ._workspace import WorkspaceError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ._workspace import Marker

# The exports that a site's data team saves in the workspace: the patient
# file, and a folder holding the tables the project merges.
PATIENTS = "patients.csv"
TABLES = "tables"
# The file the linkage agent writes and the merge agent receives.
LINKAGES = file_name("linkages")
# The file the linkage agent writes and the coordinator receives, for pprl
# review.
LINKAGE_REPORT = file_name("linkage_report")
# The folder the merge agent writes for the host.
MERGED = "merged"
# The placeholder for the site's name in the file names in FILES, and for
# the site as a file's recipient.
SITE = "{site}"


@dataclass(frozen=True)
class File:
    """A file that the roles' commands read or write.

    It records the file's name in a workspace, with SITE where the name
    contains a site. It also records the role it is sent to, if any (SITE
    for the site the file is for), and whether it is a folder holding one
    CSV file per table. The role that writes a file to be sent writes it to
    outbox/<recipient>/. The recipient keeps it in its own workspace folder,
    under the same name.
    """

    name: str
    recipient: str | None = None
    folder: bool = False

    @property
    def recipient_role(self) -> str | None:
        """Return the role the file is sent to: a site, if it is sent to its site."""
        return Role.SITE if self.recipient == SITE else self.recipient

    @property
    def of_site(self) -> bool:
        """Tell whether each site has its own copy (an agent receives every site's)."""
        return SITE in self.name

    def named(self, site: str | None = None) -> str:
        """Return the file's name, for ``site`` if it is per site. Folders end in "/".

        >>> FILES["hashes"].named("site_a"), FILES["merged"].named()
        ('site_a.hashes.pprl', 'merged/')
        """
        return self.name.replace(SITE, str(site)) + ("/" if self.folder else "")

    def written(self, site: str | None = None) -> str:
        """Return where the writing role keeps the file: in its outbox, if sent.

        >>> FILES["hashes"].written("site_a"), FILES["secret"].written("site_a")
        ('outbox/linkage/site_a.hashes.pprl', 'outbox/site_a/site_a.secret.pprl')
        """
        name = self.named(site)
        if self.recipient is None:
            return name
        return outbox(self.recipient.replace(SITE, str(site)), name)

    def each(self, site: str | None = None, sites: Sequence[str] = ()) -> list:
        """Return the file's names in a workspace.

        Return ``site``'s copy. If the file is per site and no site is given,
        return each of ``sites``' copies, as an agent receives them.
        """
        if self.of_site and site is None:
            return [self.named(s) for s in sites]
        return [self.named(site)]


# Every file the roles' commands read and write, keyed by what it is.
FILES = {
    "private_key": File(file_name("private_key", SITE)),
    "public_key": File(file_name("public_key", SITE), Role.COORDINATOR),
    "secret": File(file_name("secret", SITE), SITE),
    "patients": File(PATIENTS),
    "tables_folder": File(TABLES, folder=True),
    "selected": File(file_name("selected", SITE)),
    "cleaned": File(file_name("clean", SITE)),
    "set_aside": File(f"{SITE}.set_aside.csv"),
    "hashes": File(file_name("hashes", SITE), Role.LINKAGE),
    "tables": File(file_name("tables", SITE), Role.MERGE),
    "linkages": File(LINKAGES, Role.MERGE),
    "linkage_report": File(LINKAGE_REPORT, Role.COORDINATOR),
    "merged": File(MERGED, "host", folder=True),
}


def site_files(site: str) -> dict[str, str]:
    """List the files that a site's commands read and write, keyed by what they are.

    The names are as the site has them. An agent receives the files from the
    site's outbox under the same names, in its workspace folder.

    >>> site_files("site_a")["hashes"]
    'site_a.hashes.pprl'
    """
    return {
        "bundle": _bundle.bundle_name(Role.SITE, site),
        **{key: file.named(site) for key, file in FILES.items() if file.of_site},
    }


def provenance(bundle: dict) -> dict:
    """Return the provenance of a command's files.

    This is the project, bundle set and site. Every pprl file the command
    reads must belong to them, and every file it writes is for them.
    """
    return {
        "project": bundle["project"]["id"],
        "set": bundle["set"],
        "site": bundle.get("site"),
    }


def workspace_bundle(marker: Marker, folder: Path) -> dict | None:
    """Return the settings of the bundle in the workspace ``folder``.

    ``marker`` is the workspace's marker. The bundle must be for this
    workspace's project, role and site, and from the set the workspace was
    created with. Raise WorkspaceError if not. Return None for the
    coordinator's workspace, which has no bundle.
    """
    name = marker.bundle_name
    if name is None:  # This is the coordinator's workspace.
        return None
    try:
        bundle = _bundle.read(folder / name)
    except FileNotFoundError:
        raise WorkspaceError(msg.BUNDLE_MISSING(name=name)) from None
    except _bundle.BundleError as e:
        raise WorkspaceError(msg.BUNDLE_UNUSABLE(name=name, reason=str(e))) from None
    if (
        bundle["project"]["id"] != marker.project
        or bundle["role"] != marker.role
        or bundle.get("site") != marker.site
    ):
        reason = msg.BUNDLE_REASONS["other workspace"]()
    elif bundle["set"] != marker.bundle_set:
        reason = msg.BUNDLE_REASONS["replaced"]()
    else:
        return bundle
    raise WorkspaceError(msg.BUNDLE_UNUSABLE(name=name, reason=reason))


def _files(bundle: dict) -> dict[str, str]:
    """List the site's files, keyed by what they are (see site_files)."""
    return site_files(bundle["site"])


def _written(key: str, bundle: dict) -> str:
    """Return where FILES[key] is written in the workspace of ``bundle``'s role."""
    return FILES[key].written(bundle.get("site"))


@dataclass(frozen=True)
class Command:
    """A role's command and what it needs to run.

    It records the role whose guide lists the command, the function it runs,
    and the files it reads and writes (keys of FILES). It also records how to
    build the function's settings from the bundle and the fixed file names,
    and the steps of its progress bar. ``run`` adds
    the provenance and the folders.
    """

    role: Role
    function: Callable
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    settings: Callable[[dict], dict]
    steps: tuple


# Listed in the order each role uses them.
COMMANDS = {
    "select": Command(
        Role.SITE,
        _select.select,
        ("tables_folder",),
        ("selected",),
        lambda bundle: {
            "tables": bundle["tables"],
            "id_column": bundle["id"],
            "exports": TABLES,
            "output": _files(bundle)["selected"],
        },
        _select.STEPS,
    ),
    "clean": Command(
        Role.SITE,
        _clean.clean,
        ("patients", "selected"),
        ("cleaned", "set_aside"),
        lambda bundle: {
            "patients": PATIENTS,
            "id_column": bundle["id"],
            "fields": bundle["fields"],
            "selected": _files(bundle)["selected"],
            "output": _files(bundle)["cleaned"],
            "flagged": _files(bundle)["set_aside"],
        },
        _clean.STEPS,
    ),
    "hash": Command(
        Role.SITE,
        _hash.hash_with_key,
        ("cleaned", "private_key", "secret"),
        ("hashes",),
        lambda bundle: {
            "patients": _files(bundle)["cleaned"],
            "secret_file": _files(bundle)["secret"],
            "private_key": _files(bundle)["private_key"],
            "fields": bundle["fields"],
            "hash_length": bundle["hash_length"],
            "fill_ratio": bundle["fill_ratio"],
            "xor_folds": bundle["xor_folds"],
            "project_salt": bundle["project_salt"],
            "record_salt": bundle["record_salt"],
            "output": _written("hashes", bundle),
        },
        _hash.STEPS,
    ),
    "pack": Command(
        Role.SITE,
        _pack.pack,
        ("selected", "cleaned"),
        ("tables",),
        lambda bundle: {
            "tables": bundle["tables"],
            "id_column": bundle["id"],
            "selected": _files(bundle)["selected"],
            "cleaned": _files(bundle)["cleaned"],
            "patients": PATIENTS,
            "flagged": _files(bundle)["set_aside"],
            "output": _written("tables", bundle),
        },
        _pack.STEPS,
    ),
    "link": Command(
        Role.LINKAGE,
        _link.link,
        ("hashes",),
        ("linkages", "linkage_report"),
        lambda bundle: {
            "hashes": FILES["hashes"].each(sites=bundle["sites"]),
            "threshold": bundle["threshold"],
            "secret_check": bundle["secret_check"],
            "output": _written("linkages", bundle),
            "report": _written("linkage_report", bundle),
            "sites": bundle["sites"],
        },
        _link.STEPS,
    ),
    "merge": Command(
        Role.MERGE,
        _merge.merge,
        ("linkages", "tables"),
        ("merged",),
        lambda bundle: {
            "linkages": LINKAGES,
            "sites": bundle["sites"],
            "id_column": bundle["id"],
            "tables": bundle["tables"],
            "output": outbox("host", MERGED),
        },
        _merge.STEPS,
    ),
}


@dataclass(frozen=True)
class RoleFiles:
    """A role's files, as keys of FILES, in the order its commands use them.

    - ``reads``: files the user saves in the workspace for the role.
    - ``writes``: files the role writes there for itself.
    - ``uploads``: files the role writes to its outbox to send.
    - ``downloads``: files the role receives from earlier roles, apart from
      its bundle.
    """

    reads: tuple[str, ...]
    writes: tuple[str, ...]
    uploads: tuple[str, ...]
    downloads: tuple[str, ...]


def _role_files(role: str) -> RoleFiles:
    commands = [command for command in COMMANDS.values() if command.role == role]
    read = dict.fromkeys(key for command in commands for key in command.reads)
    written = dict.fromkeys(key for command in commands for key in command.writes)
    return RoleFiles(
        reads=tuple(k for k in read if k not in written and not FILES[k].recipient),
        writes=tuple(k for k in written if not FILES[k].recipient),
        uploads=tuple(k for k in written if FILES[k].recipient),
        downloads=tuple(k for k in read if FILES[k].recipient_role == role),
    )


# The files of each role that runs commands on data.
ROLE_FILES = {role: _role_files(role) for role in _bundle.ROLE_KEYS}


def other_bundles(marker: Marker, folder: Path) -> list:
    """List the bundles in the workspace ``folder`` or its outbox, other than its own.

    Commands leave these bundles alone, but log them.
    """
    own = marker.bundle_name
    found = [*Path(folder).glob("*.pprl"), *Path(folder).glob(f"{OUTBOX}/*/*.pprl")]
    return sorted(
        p.relative_to(folder).as_posix()
        for p in found
        if _bundle.is_bundle_name(p.name) and p.relative_to(folder).as_posix() != own
    )


def run(command: str, marker: Marker, bundle: dict | None, folder: Path) -> int:
    """Run a role's command in the workspace ``folder`` and return the exit code.

    The command gets the bundle's settings and provenance, and the workspace
    as both its data and output folder. It stops, naming the setting, if the
    workspace has no bundle (the coordinator's) or if the bundle lacks a
    setting the command needs.
    """
    role_command = COMMANDS[command]
    try:
        settings = role_command.settings(bundle or {})
    except KeyError as e:
        (setting,) = e.args
        if bundle is None:
            return fail(
                msg.SETTING_NO_BUNDLE(command=f"pprl {command}", setting=setting)
            )
        return fail(
            msg.SETTING_MISSING(
                command=f"pprl {command}",
                setting=setting,
                name=marker.bundle_name,
            )
        )
    if other := other_bundles(marker, folder):
        info_log(msg.INFO_OTHER_BUNDLES(names=_wording.listed(other)))
    if bundle is None:  # Every role command needs a setting from the bundle.
        raise AssertionError
    with progress(command, role_command.steps):
        return run_command(
            role_command.function,
            {
                **settings,
                "provenance": provenance(bundle),
                "data_folder": folder,
                "output_folder": folder,
            },
        )
