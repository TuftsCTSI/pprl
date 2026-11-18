"""Define the constants and small checks that every part of pprl shares.

This module holds:

- the roles
- the members and columns of each kind of file
- the config keys that only pprl may set
- this release and the current time
- what counts as a number or as text in a config
- the base class for errors whose text is a message for the user
- helpers that tell whether an error is a full drive, and where it happened

This module imports nothing from pprl, so any module can use it.
"""

import errno
import traceback
from collections import Counter
from datetime import datetime
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import TypeGuard


class Role(StrEnum):
    """The roles that work in a workspace.

    Sites contribute data. Only a site's workspace names its site. The
    coordinator writes the bundles. The coordinator is often at a site too,
    taking part from a separate workspace, but nothing here assumes that.
    The agents are roles kept apart from the sites for security.
    """

    COORDINATOR = "coordinator"
    SITE = "site"
    LINKAGE = "linkage"
    MERGE = "merge"


# The settings that each role's bundle holds, beyond those every bundle holds.
ROLE_KEYS = {
    Role.SITE: (
        "site",
        "id",
        "fields",
        "hash_length",
        "fill_ratio",
        "xor_folds",
        "project_salt",
        "record_salt",
        "tables",
    ),
    Role.LINKAGE: ("sites", "threshold", "secret_check"),
    Role.MERGE: ("id", "sites", "tables"),
}


class Member(StrEnum):
    """The fixed member names used in each kind of pprl file.

    In a tables file or a selected tables file, every other member is a table.
    """

    # The only member of a bundle: the project's name and the role's settings.
    SETTINGS = "settings"
    # The only member of a private key, public key or secret file.
    KEY = "key"
    # The member of a selected tables file (written by pprl select) that lists
    # the study's patients by their site id, for pprl clean to use. The name
    # contains a space, so it can never clash with a table's name.
    COHORT = "study patients"
    # The patients in a cleaned patient file. pprl hash reads them, and pprl
    # pack reads each patient's site id and study_id.
    PATIENTS = "patients"
    STUDY_IDS = "study_ids"
    # The hashes in a hash file.
    HASHES = "hashes"
    # A linkage file's rows.
    LINKAGES = "linkages"
    # The only member of a linkage report: what link found about the
    # linkage's quality, for each pair of sites. It holds only counts.
    QUALITY = "quality"


# The id that pprl clean gives each patient in place of the site's own id. It
# is a random shuffle of 1 to N, used only for the study. The project names
# the site's id column (`id`), and that column never leaves the site.
STUDY_ID = "study_id"
# The columns that identify a record in a cleaned patient file. The site comes
# from the workspace's bundle, never from the file.
RECORD_COLUMNS = (STUDY_ID,)
# The columns of a hash file, written by pprl hash: each record's study_id and
# its hash. The file's header says which site the hashes belong to.
HASH_COLUMNS = (STUDY_ID, "hash")
# The columns of a linkage file's rows, written by pprl link.
LINKAGE_COLUMNS = ("group_id", "site", STUDY_ID)
# The linkage file's last column. It gives each group's strength, which is
# the score of its weakest pair.
SIMILARITY = "similarity"
# The character that joins the parts of a compound key, such as a record's
# site and study_id. It is ASCII's unit separator, which never appears in a
# name, id or seed.
SEPARATOR = "\x1f"

# The workspace folder for files the role sends to another role, with one
# folder inside for each recipient. Nothing else goes there; every other file
# stays in the workspace folder itself.
OUTBOX = "outbox"


def outbox(recipient: str, name: str = "") -> str:
    """Return the workspace path where a file to be sent to ``recipient`` is written.

    The recipient is a role (coordinator, linkage, merge or host) or a site.
    Without ``name``, return the recipient's folder.

    >>> outbox("merge", "linkages.pprl"), outbox("site_a")
    ('outbox/merge/linkages.pprl', 'outbox/site_a')
    """
    return "/".join(filter(None, (OUTBOX, recipient, name)))


# Config keys that only pprl itself may set. A config's keys are passed to a
# command function as arguments, so a user must not be able to set these.
RESERVED_KEYS = frozenset({"data_folder", "output_folder"})

# The release of pprl that is running. Every workspace, bundle and file
# records the release that made it.
RELEASE = version("pprl")


def is_other_release(release: str) -> bool:
    """Tell whether ``release`` differs from the running release.

    ``release`` is the release that made a workspace, bundle or file.

    >>> is_other_release(RELEASE), is_other_release("0.0.1")
    (False, True)
    """
    return release != RELEASE


def now() -> datetime:
    """Return the current time, with the computer's offset from UTC."""
    return datetime.now().astimezone()


def is_number(value: object) -> TypeGuard[float]:
    """Tell whether a config value is a number. YAML's true and false are not.

    >>> [is_number(v) for v in (1, 0.5, True, "1")]
    [True, True, False, False]
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_whole_number(value: object) -> TypeGuard[int]:
    """Tell whether a config value is a whole number. YAML's true and false are not.

    >>> [is_whole_number(v) for v in (1, 0.5, True)]
    [True, False, False]
    """
    return isinstance(value, int) and not isinstance(value, bool)


def is_share(value: object) -> TypeGuard[float]:
    """Tell whether a config value is a number from 0 to 1.

    >>> [is_share(v) for v in (0, 0.5, 1, 1.5, True)]
    [True, True, True, False, False]
    """
    return is_number(value) and 0 <= value <= 1


def is_text(value: object) -> TypeGuard[str]:
    """Tell whether a value is a string that is not empty or only whitespace.

    >>> [is_text(v) for v in ("a", "", "  ", 1, None)]
    [True, False, False, False, False]
    """
    return isinstance(value, str) and bool(value.strip())


def repeated(values: Iterable) -> list:
    """List the values that appear more than once, sorted.

    >>> repeated(["b", "a", "b", "c", "a", "b"])
    ['a', 'b']
    """
    return sorted(value for value, count in Counter(values).items() if count > 1)


class UserFacingError(Exception):
    """Base class for errors whose str() is the message to show the user.

    The text is either a message from the message catalog, or the plain
    phrase that a message takes.
    """


def is_disk_full(error: OSError) -> bool:
    """Tell whether an OSError means the drive is full or a quota is used up."""
    full = (errno.ENOSPC, getattr(errno, "EDQUOT", None))
    return error.errno in full or getattr(error, "winerror", None) in (39, 112)


def error_place(error: BaseException) -> str:
    """Return where in pprl an error happened, as "<file name>, line <number>".

    This is the innermost place in pprl's own code, because a place in a
    library's code means little to the coordinator. The error's text is
    never included, because it can quote data.
    """
    frames = traceback.extract_tb(error.__traceback__)
    ours = [f for f in frames if "pprl" in Path(f.filename).parts] or frames
    if not ours:
        return "pprl"
    return f"{Path(ours[-1].filename).name}, line {ours[-1].lineno}"


def owner_name(role: str, site: str | None = None) -> str:
    """Name a workspace's owner, for file names: the site, or else the role.

    >>> owner_name("site", "site_a"), owner_name("linkage")
    ('site_a', 'linkage')
    """
    return site if role == Role.SITE and site else str(role)


def log_name(role: str, site: str | None = None) -> str:
    """Return the name of a workspace's log, such as site_a.log or linkage.log."""
    return f"{owner_name(role, site)}.log"
