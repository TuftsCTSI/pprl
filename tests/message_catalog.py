"""Check and document pprl's message catalog.

``render_markdown`` writes the messages document, which lists every
message for review. ``literal_messages`` finds text shown to users that is
written outside the catalog. The maintainability tests use both.

To regenerate the messages document:

    UPDATE_FIXTURES=1 uv run pytest -k test_messages_doc_current
"""

import ast
import re
import string
from typing import TYPE_CHECKING

from pprl import _messages as msg
from pprl import _wording
from pprl._io import read_text
from tests.helpers import SOURCE_ROOT, markdown_cell, markdown_page, python_files

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pprl._wording import Message

DOC_PATH = SOURCE_ROOT / "docs" / "messages.md"
SOURCE_DIR = SOURCE_ROOT / "src" / "pprl"
# The modules that only the commands on the project use. The coordinator runs
# those commands and writes the project's settings, so these modules' fixes
# may name settings.
PROJECT_MODULES = ("_project", "_guides", "_sweep", "_review", "synth")


def _code_files() -> list[Path]:
    """List every module of pprl."""
    return python_files("src/pprl")


# Functions that show their arguments to the user.
SHOWING_FUNCTIONS = {
    "fail",
    "ok_user",
    "note_user",
    "warn_user",
    "error_user",
    "info_block",
    "scenario_problem",
    "_Problem",
}
# ValidationContext methods, which show their label and detail.
SHOWING_METHODS = {"ok", "fail", "skip"}


def named_messages() -> dict[str, Message]:
    """Return every Message in the catalog, keyed by name."""
    return {
        name: value
        for name, value in vars(msg).items()
        if isinstance(value, _wording.Message) and not name.startswith("_")
    }


def _is_text(node: ast.expr) -> bool:
    """Tell whether an argument is literal text: a string or an f-string."""
    if isinstance(node, ast.JoinedStr):
        return True
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.strip() != ""
    )


def _shown_arguments(node: ast.AST) -> list[ast.expr]:
    """List the arguments of ``node`` that are shown to users.

    Return none unless ``node`` calls a function that shows text.
    """
    if not isinstance(node, ast.Call):
        return []
    func = node.func
    if isinstance(func, ast.Name):
        name = func.id
        shows = name in SHOWING_FUNCTIONS or name == "print"
    elif isinstance(func, ast.Attribute):
        name = func.attr
        receiver = func.value
        shows = (
            name in SHOWING_METHODS
            and isinstance(receiver, ast.Name)
            and receiver.id.endswith("ctx")
        )
    else:
        return []
    if name == "ScenarioError":
        return node.args[1:]
    return node.args if shows else []


def literal_messages() -> list[str]:
    """Find text shown to users but written outside the catalog, as "file:line: text".

    Look at the arguments of the functions that show text (fail, ok_user,
    ctx.fail, scenario_problem, print, ...), everywhere except the message
    catalog and the UI module. A ScenarioError's location (its first
    argument) is a path into the scenario's YAML, not a message.
    """
    found = []
    for path in _code_files():
        # The catalog holds the wording, and the UI module lays it out.
        if path.name in ("_messages.py", "_ui.py"):
            continue
        tree = ast.parse(read_text(path), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for argument in _shown_arguments(node):
                if _is_text(argument):
                    relative = path.relative_to(SOURCE_ROOT)
                    found.append(
                        f"{relative}:{node.lineno}: {ast.unparse(argument)[:70]}"
                    )
    return found


def unused_messages() -> list[str]:
    """List catalog messages, labels and kinds that no module of pprl uses."""
    source = "\n".join(
        read_text(path) for path in _code_files() if path.name != "_messages.py"
    )
    used = set(re.findall(r"\bmsg\.([A-Z][A-Z0-9_]+)\b", source))
    unused = sorted(set(named_messages()) - used)
    for group in (msg.Label, msg.Kind):
        names = [name for name in vars(group) if name.isupper()]
        unused += [
            f"{group.__name__}.{name}"
            for name in names
            if not re.search(rf"\b{group.__name__}\.{name}\b", source)
        ]
    return unused


def catalog_problems() -> list[str]:
    """List every way the catalog breaks its own rules, as sentences."""
    problems = []
    seen = {}
    for name, message in named_messages().items():
        words = (message.text, message.fix)
        if words in seen:
            problems.append(f"{name} and {seen[words]} say the same")
        seen[words] = name
    problems.extend(f"{name} is not used by pprl" for name in unused_messages())
    problems.extend(
        f"{name} has placeholders: make it a Message" for name in unfilled_templates()
    )
    return problems


def _texts(name: str, value: object) -> Iterator[tuple[str, str]]:
    """List each string in a catalog entry, with where it is.

    Strings inside a dict, a tuple or a list are included.
    """
    if isinstance(value, str):
        yield name, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _texts(f"{name}[{key!r}]", item)
    elif isinstance(value, tuple | list):
        for i, item in enumerate(value):
            yield from _texts(f"{name}[{i}]", item)


def unfilled_templates() -> list[str]:
    """Name the catalog's plain strings that have {placeholders}.

    Each of them should be a Message, called with values, as every other
    template is.
    """
    entries = [(n, v) for n, v in vars(msg).items() if n.isupper() and n[0] != "_"]
    for group in (msg.Label, msg.Kind):
        entries += [
            (f"{group.__name__}.{n}", v) for n, v in vars(group).items() if n.isupper()
        ]
    return [
        where
        for name, value in entries
        for where, text in _texts(name, value)
        if any(field is not None for _, field, _, _ in string.Formatter().parse(text))
    ]


def render_markdown() -> str:
    """Render the text of the messages document."""
    lines = [
        (
            "Every message a user can see is defined in src/pprl/_messages.py; this "
            "page lists them for review. A problem has a fix, shown after it as "
            "`To fix:`. Words in {braces} are "
            "filled in with the details of each case: names of files, columns and "
            "settings, counts and row numbers, but never a value from a patient "
            "file, an id of any kind, or the secret. A count is shown with "
            "its noun, singular or plural: `{count:row(s)}` reads 1 row, or "
            "1,200 rows, and `{rows:row(s)}` reads row 7, or rows 7, 9. A problem "
            "with the project's settings, which only the coordinator can put "
            "right, asks pprl's users to send the log, and gives the fix the "
            "commands on the project show in brackets. Where a "
            "message holds text the user supplied, such as a folder's name or "
            "an export's column names, the terminal shows it, and the log has "
            f"{_wording.WITHHELD} in its place. Technical details are written to "
            f"the log only, each after {msg.INFO_TAG}."
        ),
        "",
        (
            "`make docs` (or `python -m tests.usability_cases`) writes "
            "build/input-handling.md, which shows the message each kind of input "
            "gets."
        ),
    ]
    section = None
    for message in named_messages().values():
        if message.section != section:
            section = message.section
            lines += ["", f"## {section}", ""]
            lines += ["| Message | To fix |", "| --- | --- |"]
        fix = message.user_fix
        if message.kind == _wording.SETTINGS and message.fix:
            fix += f" (In a command on the project: {message.fix})"
        lines.append(f"| {markdown_cell(message.text)} | {markdown_cell(fix)} |")

    lines += [
        "",
        "## Words used inside messages",
        "",
        "| Where | Words |",
        "| --- | --- |",
    ]
    for field_type, (description, how) in msg.FORMATS.items():
        lines.append(
            f"| a value not in its format, a {field_type} field "
            f"| {markdown_cell(description)}. "
            f"{markdown_cell(how)} |"
        )
    for words in msg.REPAIRS.values():
        lines.append(f"| what clean repaired | {markdown_cell(words)} |")
    for reason in msg.REASONS.values():
        lines.append(f"| why clean set a row aside | {markdown_cell(reason.text)} |")
    for words in msg.FILE_KINDS.values():
        lines.append(f"| a kind of pprl file | {markdown_cell(words)} |")
    for words in msg.INSPECT_LABELS.values():
        lines.append(f"| inspect, a line it shows | {markdown_cell(words)} |")
    return markdown_page(
        "Every message pprl shows",
        "src/pprl/_messages.py",
        "tests/message_catalog.py",
        lines,
    )


# Words that never appear in a fix a pprl user can follow: no setting, config
# file, developer document, or troubleshooting of another role's files.
_NOT_FOR_USERS = re.compile(
    r"`|\bconfig|\bsettings?\b|project\.yml|docs/|reference\.md|\bedit"
    r"|\bask (each|whoever|the site)",
    re.IGNORECASE,
)


def _imports(path: Path) -> set[str]:
    """List the modules of pprl that the module at ``path`` imports."""
    found = set()
    for node in ast.walk(ast.parse(read_text(path))):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                found.add(node.module.split(".")[0])
            else:
                found |= {alias.name for alias in node.names}
    return found


def pprl_messages() -> dict[str, Message]:
    """List the catalog's messages that pprl's command line can show, by name.

    These are the messages used by the modules it imports, except
    PROJECT_MODULES.
    """
    modules, todo = set(), ["cli"]
    while todo:
        module = todo.pop()
        path = SOURCE_DIR / f"{module}.py"
        if module in modules or module in PROJECT_MODULES or not path.exists():
            continue
        modules.add(module)
        todo += _imports(path)
    used = set()
    for module in modules:
        text = read_text(SOURCE_DIR / f"{module}.py")
        used |= set(re.findall(r"\bmsg\.([A-Z][A-Z0-9_]+)\b", text))
    return {name: m for name, m in named_messages().items() if name in used}


def fix_problems() -> list[str]:
    """Find every fix that pprl's users could see but is not meant for them.

    Return them as sentences. A fix that users follow themselves must be in
    plain words. Any other fix must tell them to send the log to the
    coordinator.
    """
    problems = []
    for name, message in named_messages().items():
        if message.kind not in (_wording.ACT, _wording.ESCALATES, _wording.SETTINGS):
            problems.append(f"{name} has no kind of fix: {message.kind!r}")
    for name, message in pprl_messages().items():
        if message.kind != _wording.ACT:
            continue
        if found := _NOT_FOR_USERS.search(message.fix):
            problems.append(
                f"{name}'s fix says {found.group()!r}: make it escalate, or "
                f"a problem with the settings: {message.fix}"
            )
    return problems
