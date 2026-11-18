"""Check project.yml, and write the bundles and secrets made from it.

The coordinator writes project.yml in a new folder, and runs ``pprl init``
there to make the folder their workspace:

    name: Study 2026
    id: person_id                    # each site's id column
    sites: [site_a, site_b, site_c]  # also merge's tie-breaking order,
                                     # the definitive site first
    fields: {name: 0.5, dob: {weight: 0.25, type: date}}
    hash_length: 1024                # bits in each hash
    fill_ratio: 1.0                  # bits set by all fields / hash_length
    threshold: 0.80
    secret_check: true               # whether link runs the same-secret check
    tables: {...}                    # each table's selection and merge rules

``pprl bundles`` then writes one bundle per recipient into the workspace's
outbox, in the recipient's folder: outbox/site_a/site_a.pprl,
outbox/linkage/linkage.pprl and so on. Each bundle is named only for its
recipient, and holds only its role's settings:

- A site's bundle gives each table's columns, its primary key, and the rules that
  ``pprl select`` applies.
- The merge agent's bundle gives the merge rules, without those.

The project's id is the workspace's. The bundle set is a new id on each
run. The release is the installed pprl's.

No bundle holds the secret. Each site's ``pprl init`` writes the site's
public key. The coordinator saves the public keys in this workspace, as
site_a.public_key.pprl and so on. ``pprl secrets`` then makes one new
secret and locks it to each public key. It writes
outbox/site_a/site_a.secret.pprl and the others, and keeps no copy.
"""

import uuid
from typing import TYPE_CHECKING

from . import _bundle, _locking, _pprl_file, _wording
from . import _messages as msg
from ._bundle import RESERVED_SITES, bundle_name
from ._config import load_config
from ._inputs import (
    check_fields,
    check_fill_ratio,
    check_hash_length,
    check_id_column,
    check_number_in_range,
    check_pprl_input,
    check_project_salt,
    check_record_salt,
    check_sites,
    check_xor_folds,
    new_secret,
)
from ._io import display_name, for_writing_together
from ._merge import check_tables, table_columns
from ._messages import Label
from ._pprl_file import PprlFileError, file_name
from ._rules import check_site_tables
from ._shared import RELEASE, Member, Role, is_text, outbox
from ._ui import ValidationContext, add_work, advance, finish_progress, ok_user

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from ._workspace import Marker

PROJECT_FILE = "project.yml"
PROJECT_KEYS = (
    "name",
    "id",
    "sites",
    "fields",
    "hash_length",
    "fill_ratio",
    "xor_folds",
    "project_salt",
    "record_salt",
    "threshold",
    "secret_check",
    "tables",
)
# The roles other than the sites that get a bundle.
AGENTS = (Role.LINKAGE, Role.MERGE)
# The table settings that pprl select applies. Only the sites' bundles hold
# them.
SELECTION_KEYS = ("keep", "censor")


def _check_name(ctx: ValidationContext, name: object) -> bool:
    """Check `name`, the project's name as people call it."""
    if is_text(name):
        return True
    ctx.fail(Label.PROJECT_NAME, msg.PROJECT_NAME_INVALID(value=_wording.shown(name)))
    return False


def _check_secret_check(ctx: ValidationContext, value: object) -> None:
    """Check `secret_check`, which says whether link runs the same-secret check.

    The coordinator must give it, as true or false. A project that uses
    `record_salt` turns the check off, since salting each record makes it fail.
    """
    if not isinstance(value, bool):
        ctx.fail(
            Label.SECRET_CHECK,
            msg.SECRET_CHECK_INVALID(value=_wording.shown(value)),
        )


def recipients(sites: list[str], roles: Iterable[str] = AGENTS) -> list:
    """List the recipients the coordinator sends files to, as (role, site).

    The list has each site first, then each of ``roles``, which have no site.
    By default, ``roles`` is the agents, which get one bundle each.

    >>> [(str(role), site) for role, site in recipients(["site_a"])]
    [('site', 'site_a'), ('linkage', None), ('merge', None)]
    """
    return [(Role.SITE, site) for site in sites] + [(r, None) for r in roles]


def _bundle_names(sites: list[str]) -> dict[str, str]:
    """Map each recipient to its bundle's file name: the sites, then the agents."""
    return {site or role: bundle_name(role, site) for role, site in recipients(sites)}


def _site_tables(tables: dict) -> dict:
    """Return each table's settings for a site's bundle.

    These are the table's columns, its primary key, and the rules that pprl
    select applies to it.
    """
    return {
        name: {
            "columns": table_columns(rules),
            "primary_key": rules["primary_key"],
            "keep": rules.get("keep", []),
            "censor": rules.get("censor", []),
        }
        for name, rules in tables.items()
    }


def _merge_tables(tables: dict) -> dict:
    """Return each table's merge rules, for the merge agent's bundle."""
    return {
        name: {key: value for key, value in rules.items() if key not in SELECTION_KEYS}
        for name, rules in tables.items()
    }


def _bundles(project: dict, sites: list[str], project_id: str) -> dict[str, dict]:
    """Return each bundle's contents, keyed by its path in the outbox.

    Each bundle holds its role's settings from
    project.yml. A site's bundle also holds the site's own name. Each bundle
    holds only the table rules that its role applies.
    """
    common = {
        "project": {"id": project_id, "name": project["name"]},
        "set": str(uuid.uuid4()),
        "release": RELEASE,
    }
    tables = {
        Role.SITE: _site_tables(project["tables"]),
        Role.MERGE: _merge_tables(project["tables"]),
    }
    bundles = {}
    for role, site in recipients(sites):
        settings = (
            {"xor_folds": 0, "project_salt": None, "record_salt": None}
            | project
            | {
                "sites": sites,
                "site": site,
                "tables": tables.get(role),
            }
        )
        bundles[outbox(site or role, bundle_name(role, site))] = common | {
            "role": role,
            **{key: settings[key] for key in _bundle.ROLE_KEYS[role]},
        }
    return bundles


def check_project(ctx: ValidationContext, project: dict) -> bool:
    """Check every setting in project.yml. Return whether its sites are usable."""
    _check_name(ctx, project.get("name"))
    id_ok = check_id_column(ctx, project.get("id"), project.get("fields"))
    sites_ok = check_sites(
        ctx, project.get("sites"), msg.SITES_NOT_LIST, 2, RESERVED_SITES
    )
    check_fields(ctx, project.get("fields"))
    check_hash_length(ctx, project.get("hash_length"), project.get("fields"))
    check_fill_ratio(ctx, project.get("fill_ratio"))
    check_xor_folds(ctx, project.get("xor_folds", 0))
    check_project_salt(ctx, project.get("project_salt"))
    check_record_salt(ctx, project.get("record_salt"), project.get("fields"))
    check_number_in_range(
        ctx, Label.THRESHOLD, msg.THRESHOLD_INVALID, project.get("threshold")
    )
    _check_secret_check(ctx, project.get("secret_check"))
    id_column = project["id"] if id_ok else None
    if check_tables(ctx, project.get("tables"), id_column):
        check_site_tables(
            ctx, _site_tables(project["tables"]), id_column, Label.SELECTION
        )
    return sites_ok


def write_bundles(marker: Marker, folder: Path) -> int:
    """Run ``pprl bundles``: write every role's bundle to the outbox.

    Check project.yml in the workspace ``folder``, whose marker is
    ``marker``. Then write the bundles for the workspace's project. Return
    the exit code.
    """
    project = load_config(PROJECT_FILE, PROJECT_KEYS)
    if isinstance(project, int):
        return project

    ctx = ValidationContext()
    if check_project(ctx, project):
        for who, name in _bundle_names(project["sites"]).items():
            label = Label.BUNDLE(who=who)
            if (folder / outbox(who, name)).exists():
                ctx.fail(label, msg.BUNDLE_EXISTS(name=outbox(who, name)))
    if ctx.conclude():
        return 1

    bundles = _bundles(project, project["sites"], marker.project)
    add_work(len(bundles))
    for path, contents in bundles.items():
        _bundle.write(folder / path, contents)
        advance()
    finish_progress()
    ok_user(
        msg.WROTE_BUNDLES(
            names=_wording.listed(display_name(folder / p) for p in bundles)
        )
    )
    return 0


def write_secrets(marker: Marker, folder: Path) -> int:
    """Run ``pprl secrets``: lock a new secret to each site's public key.

    Make one new secret. Lock it to the public key of each site in
    project.yml; the keys are saved in the workspace ``folder``, whose marker
    is ``marker``. Write each site's secret file to the outbox. The secret is
    not kept anywhere else, so each run makes a different secret. Return the
    exit code.
    """
    project = load_config(PROJECT_FILE, PROJECT_KEYS)
    if isinstance(project, int):
        return project

    ctx = ValidationContext()
    sites = project.get("sites")
    if not (check_sites(ctx, sites, msg.SITES_NOT_LIST, 2, RESERVED_SITES)):
        sites = []
    if not isinstance(sites, list):  # check_sites has already ensured a list
        raise TypeError(sites)
    secret = new_secret()
    locked, sets, of_project = {}, {}, {"project": marker.project}
    for site in sites:
        label = Label.SITE_KEY(site=site)
        name = outbox(site, file_name("secret", site))
        if (folder / name).exists():
            ctx.fail(label, msg.SECRET_EXISTS(name=name))
        key = check_pprl_input(
            ctx,
            label,
            file_name("public_key", site),
            folder,
            "public_key",
            provenance=of_project,
            site=site,
        )
        if key:
            sets[site] = key.header["set"]
            with ctx.attempt(label, PprlFileError):
                locked[site] = key, _locking.lock(secret, key)
    if len(set(sets.values())) > 1:
        ctx.fail(Label.KEYS, msg.KEYS_OF_SEVERAL_SETS())
    if ctx.conclude():
        return 1

    paths = {site: folder / outbox(site, file_name("secret", site)) for site in sites}
    add_work(len(locked))
    with for_writing_together(*paths.values()):
        for site, (key, member) in locked.items():
            _pprl_file.write(
                paths[site],
                "secret",
                {Member.KEY: member},
                provenance=key.header,
                inputs=[key],
            )
            advance()
    finish_progress()
    ok_user(
        msg.WROTE_SECRETS(
            names=_wording.listed(display_name(path) for path in paths.values())
        )
    )
    return 0
