"""Read and write bundles, which hold one role's settings for one project.

The coordinator writes all of a project's bundles with ``pprl bundles``.
Each bundle gives one person exactly the settings their role needs, and
nothing that belongs to another role.

A bundle is a pprl file of kind ``bundle``. It is named for its recipient
only: site_a.pprl, linkage.pprl, merge.pprl. Its header gives:

- the project
- the bundle set, a new id each time ``pprl bundles`` writes the bundles
- the pprl release it is for
- its role, and for a site's bundle, the site

Its one member, ``settings``, holds the project's name and the role's
settings:

    {"name": "Study 2026", "site": "site_a", "id": "person_id",
     "fields": {"name": 0.5, "dob": {"weight": 0.3, "type": "date"}},
     "tables": {"scores": ["person_id", "score"]}}

No bundle holds the secret, which reaches each site separately. zlib's own
checksum catches a damaged bundle. The set id catches a bundle that was
replaced by one from a later set.
"""

from typing import TYPE_CHECKING

from . import _messages as msg
from . import _pprl_file
from ._io import validated_site
from ._shared import (
    RELEASE,
    ROLE_KEYS,
    Member,
    Role,
    UserFacingError,
    is_other_release,
    is_text,
)

if TYPE_CHECKING:
    from pathlib import Path

# The settings in a site's bundle that control hashing. No log may hold them.
HASHING_KEYS = (
    "fields",
    "hash_length",
    "fill_ratio",
    "xor_folds",
    "project_salt",
    "record_salt",
)


class BundleError(UserFacingError):
    """Raised for a bundle that pprl cannot use. str() gives the reason."""


# Names that no site may use. A site is named in its bundle, its guide and
# its outbox folder, so it cannot share a name with another recipient
# (coordinator, linkage, merge, host) or with link's output, linkages.pprl.
RESERVED_SITES = (Role.COORDINATOR, Role.LINKAGE, Role.MERGE, "host", "linkages")


def bundle_name(role: str, site: str | None = None) -> str:
    """Return the file name of a role's bundle, which is the recipient's name.

    >>> bundle_name(Role.SITE, "site_a"), bundle_name(Role.LINKAGE)
    ('site_a.pprl', 'linkage.pprl')
    """
    return f"{site if role == Role.SITE else Role(role)}.pprl"


def is_bundle_name(name: str) -> bool:
    """Tell whether ``name`` is the name of a bundle for some site or agent.

    A bundle's name has only one dot. The one other file named that way,
    linkages.pprl, is not a bundle.

    >>> names = ("site_b.pprl", "site_b.hashes.pprl", "linkages.pprl")
    >>> [is_bundle_name(n) for n in names]
    [True, False, False]
    """
    stem, _dot, extension = name.rpartition(".")
    return extension == "pprl" and bool(stem) and "." not in stem and stem != "linkages"


def write(path: Path, contents: dict) -> None:
    """Write a bundle to ``path``. ``contents`` has the form ``read`` returns."""
    role = contents["role"]
    settings = {key: contents[key] for key in ROLE_KEYS[role]}
    _pprl_file.write(
        path,
        "bundle",
        {Member.SETTINGS: {"name": contents["project"]["name"], **settings}},
        provenance={
            "project": contents["project"]["id"],
            "set": contents["set"],
            "site": contents.get("site"),
        },
        role=role,
    )


def _contents(header: dict, settings: object) -> dict | None:
    """Build a bundle's contents from its header and settings.

    Return None if they do not form a valid bundle for the role.
    """
    role = header.get("role")
    if not (role in ROLE_KEYS and isinstance(settings, dict)):
        return None
    role = Role(role)
    if not (_settings_fit(role, settings) and _site_fits(role, settings, header)):
        return None
    return {
        "project": {"id": header["project"], "name": settings["name"]},
        "set": header["set"],
        "release": header["release"],
        "role": Role(role),
        **{key: settings[key] for key in ROLE_KEYS[role]},
    }


def _settings_fit(role: Role, settings: dict) -> bool:
    """Tell whether ``settings`` has a name and exactly the keys ``role`` needs."""
    return set(settings) == {"name", *ROLE_KEYS[role]} and is_text(settings["name"])


def _site_fits(role: Role, settings: dict, header: dict) -> bool:
    """Tell whether a bundle's site fits its role.

    A site's bundle must name a valid site, the same one as its header. Any
    other bundle must name no site.
    """
    if role != Role.SITE:
        return header["site"] is None
    try:
        validated_site(settings["site"])
    except ValueError:
        return False
    return settings["site"] == header["site"]


def read(path: Path) -> dict:
    """Read and check the bundle at ``path``.

    The file must be a complete bundle for this release of pprl. Return the
    project (its id and name), the bundle set, the release, the role and the
    role's settings.

    Raise BundleError if the bundle is not valid, and FileNotFoundError if
    there is no file. The commands that use the settings check the settings
    themselves.
    """
    try:
        pprl_file = _pprl_file.read(path, check_release=False)
    except FileNotFoundError:
        raise
    except OSError, _pprl_file.PprlFileError:
        raise BundleError(msg.BUNDLE_REASONS["not a bundle"]()) from None
    header = pprl_file.header
    contents = None
    if header["kind"] == "bundle":
        contents = _contents(header, pprl_file.members.get(Member.SETTINGS))
    if contents is None:
        raise BundleError(msg.BUNDLE_REASONS["not a bundle"]())
    if is_other_release(header["release"]):
        raise BundleError(
            msg.BUNDLE_REASONS["release"](release=header["release"], version=RELEASE)
        )
    return contents
