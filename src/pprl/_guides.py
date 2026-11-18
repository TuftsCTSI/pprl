"""Check deployment.yml, and render the guides that ``pprl guides`` writes from it.

Each role follows a guide, which is one HTML page. pprl renders each guide
from the role's template, using the project's settings (project.yml) and
the deployment's details. The coordinator writes those details in
deployment.yml, next to project.yml:

    contacts:  # one person for each role, and for each of the project's sites
      coordinator: {name: Jo Example, email: jo@example.org}
      site_a: {name: Ana Example, email: ana@example.org}
      site_b: {name: Ben Example, email: ben@example.org}
      linkage: {name: Lee Example, email: lee@example.org}
      merge: {name: Max Example, email: max@example.org}
      host: {name: Hal Example, email: hal@example.org}
    times:  # planned times, for scheduling; no command reads them
      bundles: 2026-11-16 09:00  # bundles and guides sent
      secrets: 2026-11-16 09:30  # secrets generated and sent
      hash: 2026-11-16 12:00     # hashes shared
      link: 2026-11-16 14:00     # linking finished
      review: 2026-11-16 14:30   # linkage reviewed, go-ahead sent
      pack: 2026-11-16 15:00     # tables packed and shared
      merge: 2026-11-16 16:30    # merging finished
      destroy: 2026-11-16 17:00  # workspaces destroyed, receipts sent
    wheel: https://...     # optional: where pprl is installed from, and its
                           # #sha256=... if known

``pprl guides`` writes a guide for each recipient into its outbox folder,
next to its bundle: outbox/site_a/site_a.guide.html and one for each other
site, outbox/linkage/linkage.guide.html, outbox/merge/merge.guide.html and
outbox/host/host.guide.html. The coordinator's
own guide, coordinator.guide.html, is never sent, so it goes in the
workspace itself. Every guide names the installed pprl release, as the
bundles do. A guide holds nothing from another role's bundle unless its own
recipient needs it.

Wherever a guide says to send a file, it names the person who receives it.
Questions and problems always go to the coordinator.
"""

import html
import itertools
import re
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

from anyascii import anyascii

from . import _messages as msg
from . import _wording
from ._bundle import bundle_name
from ._commands import FILES, LINKAGE_REPORT, MERGED, ROLE_FILES, SITE
from ._config import load_config
from ._dates import as_datetime
from ._destroy import RECEIPTS, receipt_name
from ._io import display_name, for_writing, read_text
from ._merge import column_rules, merged_columns, table_columns
from ._messages import Label
from ._project import AGENTS, PROJECT_FILE, PROJECT_KEYS, check_project, recipients
from ._review import HISTOGRAM
from ._rules import rendered
from ._schema import field_types
from ._shared import OUTBOX, RELEASE, Role, is_text, log_name, now, outbox, owner_name
from ._ui import ValidationContext, add_work, advance, finish_progress, ok_user
from ._workspace import MARKER_NAME

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

DEPLOYMENT_FILE = "deployment.yml"
DEPLOYMENT_KEYS = ("contacts", "times", "wheel")
# The project's planned times, in the order the project runs, for scheduling.
# Each is named for a command. No command reads these times.
TIMES = ("bundles", "secrets", "hash", "link", "review", "pack", "merge", "destroy")
# Every role other than the sites that gets a guide: the agents (who each
# have a bundle), the host, and the coordinator, last.
GUIDE_ROLES = (*AGENTS, "host", Role.COORDINATOR)
# The roles other than the sites that each name a contact in deployment.yml.
CONTACT_ROLES = (Role.COORDINATOR, *AGENTS, "host")
TEMPLATES = Path(__file__).parent / "templates"
WHEEL_URL = (
    "https://github.com/TuftsCTSI/pprl/releases/download/"
    "v{release}/pprl-{release}-py3-none-any.whl"
)
# How a guide shows when it was generated: local time, with no time zone.
GENERATED_FORMAT = "%Y-%m-%d %H:%M:%S"
# What a value of each field type must look like in a site's patient file.
_FIELD_FORMATS = {
    "text": "As your records have it: pprl ignores case, accents, punctuation "
    "and spaces.",
    "date": "Written yyyy-mm-dd, such as 1990-03-15.",
    "zip": "5 digits, such as 02111. In Excel, format the column as text, so "
    "that a leading 0 is kept.",
    "state": "2 capital letters, such as MA.",
}


def guide_name(role: str, site: str | None = None) -> str:
    """Return the file name of a role's guide.

    >>> guide_name(Role.SITE, "site_a"), guide_name("host")
    ('site_a.guide.html', 'host.guide.html')
    """
    return f"{site or role}.guide.html"


def _guide_path(role: str, site: str | None = None) -> str:
    """Return where ``pprl guides`` writes a role's guide.

    In the coordinator's workspace, every guide goes in its recipient's
    outbox folder to be sent, except the coordinator's own.
    """
    name = guide_name(role, site)
    return name if role == Role.COORDINATOR else outbox(site or role, name)


def workspace_name(project_name: str, role: str, site: str | None = None) -> str:
    """Name the workspace folder that the guide tells its reader to create.

    >>> workspace_name("Study 2026", Role.SITE, "site_a")
    'study-2026-site_a'
    >>> workspace_name("Étude 2026", Role.LINKAGE)
    'etude-2026-linkage'
    >>> workspace_name("!", Role.MERGE)
    'project-merge'
    """
    ascii_name = anyascii(project_name).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-") or "project"
    return f"{slug}-{site or role}"


# ---------------------------------------------------------------------------
# deployment.yml
# ---------------------------------------------------------------------------


def contact_names(sites: list[str]) -> list[str]:
    """List whom deployment.yml names a contact for, in the order it shows them.

    >>> contact_names(["site_a", "site_b"])
    ['coordinator', 'site_a', 'site_b', 'linkage', 'merge', 'host']
    """
    first, *others = CONTACT_ROLES
    return [str(first), *sites, *map(str, others)]


def _is_person(value: object) -> bool:
    """Say whether ``value`` gives a name and an email address, and no more."""
    return (
        isinstance(value, dict)
        and set(value) == {"name", "email"}
        and all(is_text(v) for v in value.values())
        and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value["email"]) is not None
    )


def _check_contacts(
    ctx: ValidationContext, contacts: object, sites: list[str] | None
) -> None:
    """Check that ``contacts`` names one valid person for each role and site.

    If project.yml's sites are not valid, ``sites`` is None: then only the
    roles other than the sites are checked for, and any other entry may be a
    site.
    """
    expected = contact_names(sites or [])
    keys = _wording.listed(contact_names(sites or ["each site"]))

    def fail(problem: str) -> None:
        ctx.fail(Label.CONTACTS, msg.CONTACTS_INVALID(keys=keys, problem=problem))

    if not isinstance(contacts, dict) or not contacts:
        fail(msg.CONTACTS_PROBLEMS["not contacts"](value=_wording.shown(contacts)))
        return
    named = list(map(str, contacts))
    problems = msg.CONTACTS_PROBLEMS
    if missing := [k for k in expected if k not in named]:
        fail(problems["missing"](keys=_wording.listed(missing)))
    if sites and (unknown := [k for k in named if k not in expected]):
        fail(problems["unknown"](keys=_wording.listed(unknown)))
    if invalid := [str(k) for k, v in contacts.items() if not _is_person(v)]:
        fail(problems["invalid"](keys=_wording.listed(invalid)))


def _check_times(ctx: ValidationContext, times: object) -> None:
    keys = _wording.listed(TIMES)
    if not isinstance(times, dict):
        problem = msg.TIMES_PROBLEMS["not times"](value=_wording.shown(times))
        ctx.fail(Label.TIMES, msg.TIMES_INVALID(keys=keys, problem=problem))
        return
    wrong = [key for key in TIMES if as_datetime(times.get(key)) is None]
    unknown = sorted(map(str, set(times) - set(TIMES)))
    if wrong or unknown:
        problem = (
            msg.TIMES_PROBLEMS["wrong"](keys=_wording.listed(wrong))
            if wrong
            else msg.TIMES_PROBLEMS["unknown"](keys=_wording.listed(unknown))
        )
        ctx.fail(Label.TIMES, msg.TIMES_INVALID(keys=keys, problem=problem))
        return
    for earlier, later in itertools.pairwise(TIMES):
        earlier_at, later_at = as_datetime(times[earlier]), as_datetime(times[later])
        if not (earlier_at and later_at):  # Already reported by the check above
            raise AssertionError
        if later_at < earlier_at:
            ctx.fail(
                Label.TIMES,
                msg.TIMES_OUT_OF_ORDER(
                    later=later,
                    later_time=times[later],
                    earlier=earlier,
                    earlier_time=times[earlier],
                    keys=keys,
                ),
            )
            return


def _check_wheel(ctx: ValidationContext, wheel: object, release: str) -> None:
    if wheel is None:
        return
    if isinstance(wheel, str) and re.fullmatch(
        r"https://\S+\.whl(#sha256=[0-9a-f]{64})?", wheel
    ):
        return
    ctx.fail(
        Label.WHEEL, msg.WHEEL_INVALID(value=_wording.shown(wheel), release=release)
    )


def check_deployment(
    ctx: ValidationContext,
    deployment: dict,
    release: str,
    sites: list[str] | None,
) -> None:
    """Check every setting in deployment.yml.

    ``sites`` are project.yml's sites, or None if they are not valid.
    """
    _check_contacts(ctx, deployment.get("contacts"), sites)
    _check_times(ctx, deployment.get("times"))
    _check_wheel(ctx, deployment.get("wheel"), release)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def code(text: str, kind: str | None = None) -> str:
    """Format text as code: a file or folder, a command, or a column or value.

    ``kind`` is "file", "command", or None. The guides' tests check every
    file and command marked this way against the code.
    """
    attribute = f' class="{kind}"' if kind else ""
    return f"<code{attribute}>{_e(text)}</code>"


def _listed(items: Iterable[str]) -> str:
    """Format items as code and join them into a phrase: a, b and c."""
    *rest, last = items
    if not rest:
        return last
    if len(rest) == 1:
        return f"{rest[0]} and {last}"
    return ", ".join(rest) + " and " + last


def _person(person: dict) -> str:
    """Format a contact as a name with an email link: Jo Example (jo@example.org)."""
    email = _e(person["email"])
    return f'{_e(person["name"])} (<a href="mailto:{email}">{email}</a>)'


def _when(value: object) -> str:
    """Format a time the way a guide shows it: 09:00 on Monday 16 November 2026."""
    moment = as_datetime(value)
    if moment is None:  # Never: guides are made only from checked times.
        raise AssertionError
    return f"{moment:%H:%M on %A} {moment.day} {moment:%B %Y}"


def _patient_columns(id_column: str, fields: dict) -> str:
    rows = [
        (
            code(id_column),
            (
                "Your own id for the patient, as your records have it. Never empty, "
                "and on one row only."
            ),
        )
    ]
    rows += [
        (code(name), _e(_FIELD_FORMATS[kind]) + " Empty if unknown.")
        for name, kind in field_types(fields).items()
    ]
    return "\n".join(f"<tr><td>{c}</td><td>{d}</td></tr>" for c, d in rows)


def _site_tables(tables: dict) -> str:
    folder = FILES["tables_folder"].named()
    items = []
    for name, rules in tables.items():
        listed = _listed(code(c) for c in table_columns(rules))
        key = _listed(code(c) for c in rules["primary_key"])
        text = (
            f"<li>{code(f'{folder}{name}.csv', 'file')}: exactly the columns "
            f"{listed}, in any order; one row for each {key}."
        )
        if rules.get("keep"):
            kept = _listed(rendered(rule, code, _listed) for rule in rules["keep"])
            text += (
                ' <code class="command">pprl select</code> keeps the rows where '
                f"{kept}."
            )
        if rules.get("censor"):
            censored = _listed(code(c) for c in rules["censor"])
            text += f" It empties {censored} in every row."
        items.append(text + "</li>")
    return "\n".join(items)


def _merged_tables(rules: dict, folder: str) -> str:
    items = []
    for name, table in rules.items():
        columns = merged_columns(table["primary_key"], column_rules(table))
        listed = _listed(code(c) for c in columns)
        key = _listed(code(c) for c in table["primary_key"])
        items.append(
            f"<li>{code(f'{folder}/{name}.csv', 'file')}: the columns {listed}; "
            f"one row for each {key}.</li>"
        )
    return "\n".join(items)


def _values(
    project: dict,
    deployment: dict,
    release: str,
    role: str,
    site: str | None = None,
    *,
    generated: datetime,
) -> dict:
    """Return every placeholder value a template may use, escaped or as HTML."""
    times = {f"time_{k}": _when(deployment["times"][k]) for k in TIMES}
    sites = project["sites"]
    wheel = deployment.get("wheel") or WHEEL_URL.format(release=release)
    contacts = deployment["contacts"]
    contact = contacts[Role.COORDINATOR]
    values = {
        "project": _e(project["name"]),
        "release": _e(release),
        "generated": _e(f"{generated:{GENERATED_FORMAT}}"),
        "role_title": _e(
            msg.owner(role, site) if role == Role.SITE else msg.RECIPIENTS[role]
        ),
        # The coordinator, whom every role writes to with questions
        "contact_name": _e(contact["name"]),
        "contact": _person(contact),
        # Whom each role sends files to
        **{f"contact_{r}": _person(contacts[r]) for r in CONTACT_ROLES},
        "install": code(f"uv tool install {wheel}", "command"),
        "folder": code(workspace_name(project["name"], role, site), "file"),
        "init": code("pprl init", "command"),
        "destroy": code("pprl destroy", "command"),
        "deleted_at_end": _e(msg.WORKSPACE_DELETED_AT_END),
        "destroy_limits": _e(msg.DESTROY_LIMITS),
        "receipt": code(
            f"{RECEIPTS}/{receipt_name(owner_name(role, site), '<date>', 1)}", "file"
        ),
        "log": code(log_name(role, site), "file"),
        "marker": code(MARKER_NAME, "file"),
        "outbox": code(f"{OUTBOX}/", "file"),
        "sites": _listed(_e(s) for s in sites),
        "site_count": len(sites),
        "id": code(project["id"]),
        **times,
    }
    if role in (Role.SITE, *AGENTS):
        values["bundle"] = code(bundle_name(role, site), "file")
    if role in ROLE_FILES:
        values |= _file_values(role, site, sites)
    if role == Role.SITE:
        values |= {
            "site": _e(site),
            "patient_columns": _patient_columns(project["id"], project["fields"]),
            "site_tables": _site_tables(project["tables"]),
            "key_out": code(FILES["public_key"].written(site), "file"),
            "inspect_hashes": code(
                f"pprl inspect {FILES['hashes'].written(site)}", "command"
            ),
        }
    if role == Role.MERGE:
        values["merged_tables"] = _merged_tables(
            project["tables"], outbox("host", MERGED)
        )
    if role == "host":
        values |= {
            "merged": code(FILES["merged"].named(), "file"),
            "merged_tables": _merged_tables(project["tables"], MERGED),
        }
    if role == Role.COORDINATOR:
        values |= _coordinator_values(sites, contacts)
        values |= {
            command: code(f"pprl {command}", "command")
            for command in ("synth", "sweep", "bundles", "secrets", "guides")
        }
        values |= {
            "linkage_report": code(LINKAGE_REPORT, "file"),
            "histogram": code(HISTOGRAM, "file"),
            "review": code("pprl review", "command"),
        }
    return values


def _file_values(role: str, site: str | None, sites: list[str]) -> dict:
    """List a role's files, keyed by what they are (the keys of FILES).

    - A file the role reads or writes is listed by its name.
    - A file the role uploads is listed by its name in the outbox, under the
      key with ``_out`` added.
    - A file the role downloads is listed by its name, or as a list of each
      site's copy together with its site, under the key with ``_received``
      added.
    """
    files = ROLE_FILES[Role(role)]
    values = {}
    for key in (*files.reads, *files.writes):
        values[key] = code(FILES[key].named(site), "file")
    for key in files.uploads:
        values[f"{key}_out"] = code(FILES[key].written(site), "file")
    for key in files.downloads:
        file = FILES[key]
        if file.of_site and file.recipient != SITE:
            values[f"{key}_received"] = "\n".join(
                f"<li>{code(file.named(s), 'file')}, from {_e(s)}</li>" for s in sites
            )
        else:
            values[key] = code(file.named(site), "file")
    return values


def _coordinator_values(sites: list[str], contacts: dict) -> dict:
    """List which files the coordinator's guide sends to whom."""

    def row(who: str, folder: str, files: Iterable[str]) -> str:
        names = _listed(code(outbox(folder, f), "file") for f in files)
        person = _person(contacts[folder])
        return f"<tr><td>{_e(who)}</td><td>{person}</td><td>{names}</td></tr>"

    rows = []
    for role, site in recipients(sites, (*AGENTS, "host")):
        files = [guide_name(role, site)]
        if role != "host":  # The host is the only role with no bundle.
            files.insert(0, bundle_name(role, site))
        rows.append(
            row(
                msg.owner(role, site) if site else msg.RECIPIENTS[role],
                site or role,
                files,
            )
        )
    secrets = (
        f"<tr><td>{_e(msg.owner(Role.SITE, s))}</td><td>{_person(contacts[s])}</td>"
        f"<td>{code(FILES['secret'].written(s), 'file')}</td></tr>"
        for s in sites
    )
    return {
        "project_file": code(PROJECT_FILE, "file"),
        "deployment_file": code(DEPLOYMENT_FILE, "file"),
        "own_guide": code(guide_name(Role.COORDINATOR), "file"),
        "keys": _listed(code(FILES["public_key"].named(s), "file") for s in sites),
        "secret_files": _listed(
            code(FILES["secret"].written(s), "file") for s in sites
        ),
        "secret_sends": "\n".join(secrets),
        "sends": "\n".join(rows),
    }


def render(
    project: dict,
    deployment: dict,
    release: str,
    role: str,
    site: str | None = None,
    *,
    generated: datetime,
) -> str:
    """Render a role's guide as a complete HTML page.

    ``generated`` is when ``pprl guides`` ran, which the guide shows.
    """
    values = _values(project, deployment, release, role, site, generated=generated)
    body = Template(read_text(TEMPLATES / f"{role}.html"))
    body = body.substitute(values)
    page = Template(read_text(TEMPLATES / "base.html"))
    return page.substitute(values | {"body": body})


def guides(project: dict, deployment: dict, release: str, generated: datetime) -> dict:
    """Return every guide, keyed by its path in the coordinator's workspace.

    Every guide shows the same time, ``generated``.
    """
    return {
        _guide_path(role, site): render(
            project, deployment, release, role, site, generated=generated
        )
        for role, site in recipients(project["sites"], GUIDE_ROLES)
    }


def write_guides(folder: Path) -> int:
    """Run ``pprl guides``: write every guide, and return the exit code.

    First check project.yml and deployment.yml in the coordinator's workspace
    ``folder``.
    """
    project = load_config(PROJECT_FILE, PROJECT_KEYS)
    if isinstance(project, int):
        return project
    deployment = load_config(DEPLOYMENT_FILE, DEPLOYMENT_KEYS)
    if isinstance(deployment, int):
        return deployment

    ctx = ValidationContext()
    sites_ok = check_project(ctx, project)
    check_deployment(ctx, deployment, RELEASE, project["sites"] if sites_ok else None)
    if sites_ok:
        for role, site in recipients(project["sites"], GUIDE_ROLES):
            name = _guide_path(role, site)
            label = Label.GUIDE(who=Path(name).name.split(".")[0])
            if (folder / name).exists():
                ctx.fail(label, msg.GUIDE_EXISTS(name=name))
    if ctx.conclude():
        return 1

    written = guides(project, deployment, RELEASE, now())
    add_work(len(written))
    for name, page in written.items():
        with for_writing(folder / name) as handle:
            handle.write(page)
        advance()
    finish_progress()
    ok_user(
        msg.WROTE_GUIDES(
            guides=_wording.listed(
                display_name(folder / n) for n in written if n.startswith(OUTBOX)
            ),
            own=guide_name(Role.COORDINATOR),
        )
    )
    return 0
