"""Test the guides.

Each role follows its guide, which pprl guides renders from a template. So
every command, file and folder that a guide names is checked here against
the code. Each command must be one its role can run, in the order the role
runs them. Each file must have the name that pprl reads or writes it by. The
example project's rendered guides are stored with the expected outputs.
"""

import re
from datetime import datetime
from html.parser import HTMLParser

import pytest

from pprl import _commands
from pprl._bundle import bundle_name
from pprl._commands import (
    LINKAGE_REPORT,
    LINKAGES,
    MERGED,
    PATIENTS,
    TABLES,
    site_files,
)
from pprl._config import read_config
from pprl._destroy import RECEIPTS, receipt_name
from pprl._guides import (
    DEPLOYMENT_FILE,
    GUIDE_ROLES,
    guide_name,
    guides,
    render,
    workspace_name,
)
from pprl._io import read_text
from pprl._project import PROJECT_FILE, recipients
from pprl._review import HISTOGRAM
from pprl._shared import OUTBOX, RELEASE, log_name, outbox, owner_name
from pprl._workspace import MARKER_NAME
from pprl.cli import PROJECT_COMMANDS
from tests.helpers import EXAMPLES, EXPECTED_DIR, SOURCE_ROOT, assert_text_matches

pytestmark = pytest.mark.maintainability

EXAMPLE_GUIDES = EXPECTED_DIR / "guides"
# When the example guides were generated. It is fixed so that the stored
# example guides change only with the templates and the release. They name the
# running release, so they are regenerated after each version change.
EXAMPLE_GENERATED = datetime.fromisoformat("2026-11-16T08:30:00-05:00")
# Commands that every pprl role may run.
EVERY_ROLE = {"init", "inspect", "destroy"}
_FILE_LIKE = re.compile(r"/|\.(csv|pprl|html|txt|log|yml|md)\b")
WALKTHROUGH = SOURCE_ROOT / "docs" / "walkthrough.md"
# The line under each walkthrough step's heading that names the guide steps
# it follows, such as "> **Guide:** site guide, steps 5, 7, 8 and 9."
_CITED = re.compile(r"^> \*\*Guide:\*\* (\w+) guide, steps? ([^.]+)\.$", re.MULTILINE)
# A walkthrough step's pprl commands, without their arguments
_WALKTHROUGH_COMMAND = re.compile(r"^(pprl \w+)", re.MULTILINE)


def _guide_steps(page: str) -> dict[str, str]:
    """Split a rendered guide into its numbered steps, keyed by number."""
    found = re.findall(
        r'<h2><span class="step">(\d+)\.</span>(.*?)(?=<h2>|<footer>)', page, re.DOTALL
    )
    return dict(found)


class _Code(HTMLParser):
    """Collect every <code> element's class (None if it has none) and text."""

    def __init__(self, page: str) -> None:
        super().__init__()
        self.found: list = []
        self._open: list | None = None
        self.feed(page)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "code":
            self._open = [dict(attrs).get("class"), ""]

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._open[1] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "code" and self._open is not None:
            self.found.append(tuple(self._open))
            self._open = None


def _example() -> tuple[dict, dict]:
    project = read_config(EXAMPLES / PROJECT_FILE)
    site = project["sites"][0]
    roles = {
        guide_name("site", site): ("site", site),
        **{
            guide_name(role): (role, None)
            for role in ("linkage", "merge", "host", "coordinator")
        },
    }
    return project, roles


def _known_files(project: dict, role: str, site: str) -> set[str]:
    """Return every file and folder a role's guide may name, as the code names them."""
    sites, tables = project["sites"], project["tables"]
    folder = workspace_name(project["name"], role, site)
    common = {log_name(role, site), f"{OUTBOX}/", MARKER_NAME, folder}
    common.add(f"{RECEIPTS}/{receipt_name(owner_name(role, site), '<date>', 1)}")
    if role == "site":
        files = site_files(site)
        return common | {
            files["bundle"],
            files["private_key"],
            files["secret"],
            outbox("coordinator", files["public_key"]),
            PATIENTS,
            f"{TABLES}/",
            *(f"{TABLES}/{t}.csv" for t in tables),
            files["selected"],
            files["cleaned"],
            files["set_aside"],
            outbox("linkage", files["hashes"]),
            outbox("merge", files["tables"]),
        }
    if role == "linkage":
        return common | {
            bundle_name(role),
            *(site_files(s)["hashes"] for s in sites),
            outbox("merge", LINKAGES),
            outbox("coordinator", LINKAGE_REPORT),
        }
    if role == "merge":
        return common | {
            bundle_name(role),
            LINKAGES,
            *(site_files(s)["tables"] for s in sites),
            outbox("host", MERGED) + "/",
            *(f"{outbox('host', MERGED)}/{t}.csv" for t in tables),
        }
    if role == "host":
        return {f"{MERGED}/", *(f"{MERGED}/{t}.csv" for t in tables)}
    sent = [outbox(s, bundle_name("site", s)) for s in sites]
    sent += [outbox(s, guide_name("site", s)) for s in sites]
    sent += [outbox(r, bundle_name(r)) for r in ("linkage", "merge")]
    sent += [outbox(r, guide_name(r)) for r in ("linkage", "merge", "host")]
    return common | {
        PROJECT_FILE,
        DEPLOYMENT_FILE,
        guide_name("coordinator"),
        LINKAGE_REPORT,  # Saved from the linkage agent, for pprl review
        HISTOGRAM,  # What pprl review draws
        *(site_files(s)["public_key"] for s in sites),
        *(outbox(s, site_files(s)["secret"]) for s in sites),
        *sent,
    }


def _pprl_problem(words: list[str], role: str) -> str | None:
    """Find what is wrong with the pprl command ``words`` that a guide shows.

    Return None if nothing is wrong.
    """
    if len(words) == 1:
        return None
    allowed = EVERY_ROLE | {
        name for name, c in _commands.COMMANDS.items() if c.role == role
    }
    if role == "coordinator":
        allowed = EVERY_ROLE | set(PROJECT_COMMANDS) | set(_commands.COMMANDS)
    if words[1] not in allowed:
        return f"{role} cannot run pprl {words[1]}"
    if words[1] in ("init", "destroy") and words[2:]:
        return f"{words[1]} takes no folder or file"
    return None


def _command_problems(command: str, role: str) -> str | None:
    """Find what is wrong with a command a guide shows, or None."""
    words = command.split()
    if role == "host":
        return "the host runs nothing"
    if words[0] == "pprl":
        return _pprl_problem(words, role)
    if words[:3] == ["uv", "tool", "install"] and len(words) == 4:
        return None
    return "not a command"


def render_examples(release: str) -> dict:
    """Render the example project's guides, keyed by file name.

    Render them the way the stored copies hold them: one site's guide, and
    every other role's.
    """
    project = read_config(EXAMPLES / PROJECT_FILE)
    deployment = read_config(EXAMPLES / DEPLOYMENT_FILE)
    return {
        guide_name(role, site): render(
            project, deployment, release, role, site, generated=EXAMPLE_GENERATED
        )
        for role, site in recipients(project["sites"][:1], GUIDE_ROLES)
    }


@pytest.fixture(name="guides", scope="module")
def _guides() -> dict:
    """Render the guides of the example project once."""
    return render_examples(RELEASE)


class TestGuides:
    """The guides that pprl guides writes for each role."""

    def test_example_guides_current(self, guides: dict) -> None:
        """The stored example guides match what pprl guides renders now."""
        assert sorted(p.name for p in EXAMPLE_GUIDES.iterdir()) == sorted(guides)
        for name, page in guides.items():
            assert_text_matches(page, EXAMPLE_GUIDES / name, f"guide {name}")

    def test_walkthrough_cites_guides(self, guides: dict) -> None:
        """Each walkthrough step names the guide steps it follows, correctly.

        Every step of docs/walkthrough.md has a Guide line naming a guide and
        some of its steps. Each named step exists, and holds each pprl
        command the walkthrough step runs, so that the numbers stay right
        when a guide's steps change.
        """
        steps = read_text(WALKTHROUGH).split("\n## Step ")[1:]
        assert steps, "The walkthrough's steps were not found"
        problems = []
        for step in steps:
            heading = step.splitlines()[0]
            cited = _CITED.search(step)
            if cited is None:
                problems.append(f"Step {heading}: no Guide line")
                continue
            role, numbers = cited.group(1), re.findall(r"\d+", cited.group(2))
            name = guide_name(role, "site_a" if role == "site" else None)
            if name not in guides:
                problems.append(f"Step {heading}: no {role} guide")
                continue
            sections = _guide_steps(guides[name])
            if missing := [n for n in numbers if n not in sections]:
                problems.append(f"Step {heading}: {name} has no step {missing}")
            shown = "".join(sections.get(n, "") for n in numbers)
            problems += [
                f"Step {heading}: {command!r} is in none of the steps it names"
                for command in _WALKTHROUGH_COMMAND.findall(step)
                if command not in shown
            ]
        assert not problems, "\n".join(problems)

    def test_generated_at(self) -> None:
        """Every guide from one run shows the same time, in header and footer."""
        project = read_config(EXAMPLES / PROJECT_FILE)
        deployment = read_config(EXAMPLES / DEPLOYMENT_FILE)
        pages = guides(project, deployment, RELEASE, EXAMPLE_GENERATED)
        shown = "2026-11-16 08:30:00"
        assert len(pages) == len(recipients(project["sites"], GUIDE_ROLES))
        for name, page in pages.items():
            assert f"pprl {RELEASE}, generated at {shown}." in page, name
            assert f"This guide was generated at {shown} for " in page, name

    @pytest.mark.parametrize("name", list(_example()[1]))
    def test_guide_matches_code(self, guides: dict, name: str) -> None:
        """Every command and file a guide shows matches the code.

        Each command a guide shows can be run by its role, and the role's
        commands appear in the order the role runs them. Each file and folder
        is named the way pprl names it.
        """
        project, roles = _example()
        role, site = roles[name]
        found = _Code(guides[name]).found
        files = _known_files(project, role, site)
        problems = []
        for kind, text in found:
            if kind == "command":
                problem = _command_problems(text, role)
                if problem:
                    problems.append(f"{text!r}: {problem}")
            elif kind == "file":
                if text not in files:
                    problems.append(f"{text!r}: no such file or folder")
            elif _FILE_LIKE.search(text):
                problems.append(f"{text!r}: a file, not marked as one")
        assert not problems, "\n".join(problems)

        commands = [t.split()[1] for k, t in found if k == "command" and " " in t]
        commands = [c for c in commands if c in _commands.COMMANDS]
        own = [c for c, cmd in _commands.COMMANDS.items() if cmd.role == role]
        if role != "coordinator":
            assert list(dict.fromkeys(commands)) == own, f"{role} runs {own}, in order"
