"""Define the pprl command line, which is the boundary of what a user can reach.

Every command runs in the current folder and takes no folder argument.
init makes the current folder a workspace, and every other command runs
inside a workspace and reads only files in it:

- The commands on data read files by the fixed names their role uses.
- The commands on the project read the files the coordinator writes or
  saves, by fixed names. Only inspect takes a file's name.

The command functions take their folders as arguments. Only this layer
passes those arguments, and it always passes the workspace.

Every run in a workspace starts with ``_runs.in_workspace``.
"""

import argparse
import contextlib
import sys
from typing import TYPE_CHECKING, Never

import argcomplete
import yaml
from colorama import Fore, Style

from . import (
    _bundle,
    _destroy,
    _guides,
    _init,
    _pprl_file,
    _project,
    _review,
    _sweep,
    _wording,
)
from . import _messages as msg
from ._commands import COMMANDS as ROLE_COMMANDS
from ._commands import FILES
from ._commands import run as run_role_command
from ._inputs import check_input_csv
from ._io import display_name, validated_file_path, workspace
from ._messages import Kind, Label
from ._metrics import hash_bits
from ._runs import guarded, in_workspace, prepare_terminal
from ._shared import RELEASE, Member, UserFacingError
from ._ui import (
    ValidationContext,
    colored,
    fail,
    finish_progress,
    info_block,
    progress,
    show,
    without_repeats,
)
from ._workspace import WorkspaceError, is_workspace
from ._workspace import read as read_workspace
from .synth import STEPS as SYNTH_STEPS
from .synth import synthesize

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from ._commands import Command, File
    from ._pprl_file import PprlFile
    from ._workspace import Marker

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

# The commands on the project, which the coordinator runs. Each reads its
# config under a fixed name in the workspace, and takes no file argument:
# synth reads synth.yml, sweep reads sweep.yml, bundles reads project.yml,
# guides reads deployment.yml and review reads linkage_report.pprl.
PROJECT_COMMANDS = ("synth", "sweep", "bundles", "secrets", "guides", "review")
SYNTH_CONFIG = "synth.yml"
SWEEP_CONFIG = "sweep.yml"
# Besides those and the commands on data, there are the
# commands on the workspace. init makes the current folder a workspace, or
# lists the files of an existing one.
_COMMANDS = (*ROLE_COMMANDS, *PROJECT_COMMANDS, "inspect", "init", "destroy")


def run_inspect(file_name: str) -> int:
    """Describe a file in the workspace.

    For a pprl file, show what kind it is, whose it is, its project, bundle
    set and release, when it was written, and each member's rows and
    columns. Also show a hash file's bits, a bundle's settings, and what a
    linkage report found, as pprl review shows it. A key or a locked secret
    is never shown. For a CSV file, show its rows and
    columns. There is no way to turn a pprl file back into CSV.

    The file's name is text the user supplied, and every message marks it
    that way, so that the log withholds it.
    """
    ctx = ValidationContext()
    try:
        path = validated_file_path(
            "file", Kind.INPUT, file_name, workspace(), supplied=True
        )
    except (FileNotFoundError, TypeError, ValueError) as e:
        return ctx.fail_once(Label.INSPECT_FILE, str(e))
    if _pprl_file.is_pprl_file(path):
        try:
            pprl_file = _pprl_file.read(path, check_release=False, supplied=True)
        except _pprl_file.PprlFileError as e:
            return ctx.fail_once(Label.INSPECT_FILE, str(e))
        finish_progress()
        info_block(_described(display_name(path, supplied=True), pprl_file))
        if pprl_file.header["kind"] == "bundle":
            _show_settings(pprl_file.members.get(Member.SETTINGS) or {})
        if pprl_file.header["kind"] == "linkage_report":
            show()
            try:
                _review.show_checklist(pprl_file.members[Member.QUALITY])
            except KeyError, TypeError, ValueError:
                return fail(
                    msg.REVIEW_UNREADABLE(name=display_name(path, supplied=True))
                )
        return 0
    file_path, df = check_input_csv(
        ctx,
        (Label.INSPECT_FILE, Label.INSPECT_FILE),
        "file",
        Kind.INPUT,
        file_name,
        folder=workspace(),
        supplied=True,
    )
    if ctx.has_failures:
        ctx.conclude()
        return 1
    if df is None or file_path is None:  # Only after a failure
        raise AssertionError
    finish_progress()
    labels = msg.INSPECT_LABELS
    info_block(
        [
            (labels["file"], display_name(file_path, supplied=True)),
            (labels["type"], msg.INSPECT_CSV),
            (labels["rows"], str(len(df))),
            (labels["columns"], _wording.supplied_list(df.columns)),
        ]
    )
    return 0


def _show_settings(settings: dict) -> None:
    """Show a bundle's settings as YAML.

    The settings that control how a site hashes are withheld from the log.
    """
    show()
    for key, value in settings.items():
        text = yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True)
        for line in text.splitlines():
            show(_wording.supplied(line) if key in _bundle.HASHING_KEYS else line)


def _described(name: str, pprl_file: PprlFile) -> list[tuple[str, str]]:
    """List the (label, value) lines describing a pprl file."""
    header, labels = pprl_file.header, msg.INSPECT_LABELS
    lines = [
        (labels["file"], name),
        (labels["type"], msg.FILE_KINDS[header["kind"]]),
    ]
    if header["site"]:
        lines.append((labels["site"], header["site"]))
    if header["kind"] == "bundle":
        lines.append((labels["role"], header["role"]))
    lines += [
        (labels["project"], header["project"]),
        (labels["set"], header["set"]),
        (labels["release"], header["release"]),
        (labels["created"], _wording.when(header["created"])),
    ]
    for member in header["members"]:
        if member["type"] != "csv":
            below = header["kind"] in {"bundle", "linkage_report"}
            shown = (
                msg.INSPECT_SETTINGS(name=member["name"]) if below else member["name"]
            )
            lines.append((labels["member"], shown))
            continue
        lines.append(
            (
                labels["member"],
                msg.INSPECT_MEMBER(
                    name=member["name"],
                    rows=member["rows"],
                    columns=_wording.supplied_list(member["columns"]),
                ),
            )
        )
    hashes = pprl_file.members.get(Member.HASHES)
    if header["kind"] == "hashes" and hashes is not None and len(hashes):
        # If the hash can't be read, the other lines still describe the file.
        with contextlib.suppress(ValueError):
            bits = str(hash_bits(hashes["hash"].iloc[0]))
            lines.append((labels["bits"], _wording.supplied(bits)))
    for entry in header["inputs"]:
        lines.append(
            (
                labels["input"],
                msg.INSPECT_INPUT(
                    kind=msg.FILE_KINDS[entry["kind"]],
                    site=f" ({entry['site']})" if entry["site"] else "",
                ),
            )
        )
    return lines


# ---------------------------------------------------------------------------
# Help and entry point
# ---------------------------------------------------------------------------


def _bright(text: str) -> str:
    return f"{Style.BRIGHT}{text}{Style.RESET_ALL}"


def _dim(text: str) -> str:
    return f"{Style.DIM}{text}{Style.RESET_ALL}"


def _cmd(name: str, desc: str, pad: int) -> str:
    """Format a command or argument with its description.

    The description starts ``pad`` columns after the start of the command.
    """
    padding = " " * (pad - len(name))
    return f"  {Fore.CYAN + Style.BRIGHT}{name}{Style.RESET_ALL}{padding}{desc}"


def _pad(names: Iterable[str]) -> int:
    """Return the column where descriptions start: 2 past the longest name."""
    return max(len(name) for name in names) + 2


def _format_command_help(
    title: str,
    details: list[tuple[str, str | list[str]]],
    arguments: list[tuple[str, str | None]],
) -> str:
    """Format one command's help in the style of the splash page.

    The help shows the command's title, its (label, value) details, and its
    arguments. A label that repeats the one above it is left out.
    """
    width = max(len(label) for label, _ in details)
    lines = ["", _bright(title), ""]
    labels = without_repeats([label for label, _ in details])
    for shown, (_, value) in zip(labels, details, strict=True):
        lines.append(f"  {shown:<{width}}  {value}")
    if arguments:
        pad = _pad(name for name, _ in arguments)
        lines += ["", _bright(msg.HELP_ARGUMENTS)]
        lines += [_cmd(name, description or "", pad) for name, description in arguments]
    lines.append("")
    return "\n".join(lines)


def _positionals(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    actions = parser._actions  # noqa: SLF001 (argparse exposes them nowhere else)
    return [action for action in actions if not action.option_strings]


def _arguments_of(parser: argparse.ArgumentParser) -> list[tuple[str, str | None]]:
    """List a command's arguments, as (<name>, description)."""
    return [(f"<{action.dest}>", action.help) for action in _positionals(parser)]


def _usage_of(program: str, parser: argparse.ArgumentParser) -> str:
    """Show how a command is written, with its arguments in <angle brackets>.

    Optional arguments are also in [square brackets].
    """
    return " ".join(
        [
            program,
            *(
                f"[<{a.dest}>]" if a.nargs == "?" else f"<{a.dest}>"
                for a in _positionals(parser)
            ),
        ]
    )


def _here() -> Marker | None:
    """Read the current workspace's marker, or return None outside a workspace."""
    try:
        return read_workspace(workspace())
    except WorkspaceError:
        return None


def _files_help(command: Command, site: str) -> tuple[str, str]:
    """List what a role's ``command`` reads and writes, for its help.

    Files are named as they are in ``site``'s workspace.
    """

    def read(file: File) -> str:
        if file.folder:
            return msg.HELP_READS["folder"](name=file.named(site))
        if file.recipient == command.role and file.of_site:
            name = file.named(msg.HELP_SOME_SITE)
            return msg.HELP_READS["each site"](name=name)
        return file.named(site)

    def sent(file: File) -> str:
        name = file.written(site)
        if file.folder:
            name = msg.HELP_WRITES["folder"](name=name)
        recipient = msg.RECIPIENTS[file.recipient or ""]
        return msg.HELP_WRITES["sent"](names=name, recipient=recipient)

    written = [FILES[key] for key in command.writes]
    kept = [file.named(site) for file in written if not file.recipient]
    writes = [sent(file) for file in written if file.recipient]
    if kept:
        writes.insert(0, msg.HELP_WRITES["kept"](names=_wording.listed_and(kept)))
    reads = _wording.listed_and(read(FILES[key]) for key in command.reads)
    return reads, _wording.listed_and(writes)


def _command_help(name: str, parser: argparse.ArgumentParser) -> str:
    """Return the text for ``pprl <name> --help``.

    It says what the command does, how it is written, and the files it reads
    and writes, named as they are in this workspace.
    """
    labels = msg.HELP_LABELS
    usages = msg.HELP_USAGES.get(name) or (
        _usage_of(f"pprl {name}", parser) + msg.HELP_WHERE,
    )
    marker = _here()
    site = marker.site if marker and marker.site else msg.HELP_SOME_SITE
    if name in ROLE_COMMANDS:
        reads, writes = _files_help(ROLE_COMMANDS[name], site)
    else:
        reads, writes = msg.HELP_FILES[name]
    return _format_command_help(
        msg.HELP_COMMAND_TITLE(command=f"pprl {name}", description=msg.help_for(name)),
        [
            *((labels["usage"], usage) for usage in usages),
            (labels["reads"], reads),
            (labels["writes"], writes),
        ],
        _arguments_of(parser),
    )


def _format_help() -> str:
    """Format pprl's help, which is the splash page.

    It lists every command, grouped by what the command works on, with the
    descriptions lined up. The page is the same in any folder.
    """
    groups = msg.HELP_GROUPS
    pad = _pad(name for _, commands in groups for name, _ in commands)
    lines = ["", _bright(msg.HELP_TITLE(version=RELEASE)), "", _dim(msg.HELP_USAGE)]
    for heading, commands in groups:
        lines += ["", _bright(heading)]
        lines += [_cmd(name, description, pad) for name, description in commands]
    lines += ["", _dim(msg.HELP_FOOTER), ""]
    return "\n".join(lines)


def _example(command: str) -> str:
    """Show how a command is written, for messages about the command line."""
    return msg.HELP_EXAMPLES.get(command, msg.HELP_EXAMPLES["default"])(command=command)


class CommandLineError(UserFacingError):
    """Raised for a command line that pprl cannot run."""


class Parser(argparse.ArgumentParser):
    """An ArgumentParser whose errors are messages from the message catalog."""

    def parse(self, argv: list[str]) -> argparse.Namespace:
        """Parse the command line.

        Raise CommandLineError if the command line cannot run. The error
        names the command that was given.
        """
        args, extra = self.parse_known_args(argv)
        if extra:
            command = getattr(args, "command", None)
            raise CommandLineError(
                msg.EXTRA_ARGUMENTS(
                    command=" ".join(filter(None, (self.prog, command))),
                    extra=_wording.supplied(" ".join(extra)),
                    example=_example(command or self.prog),
                )
            )
        return args

    def error(self, message: str) -> Never:
        """Raise CommandLineError instead of printing usage and exiting."""
        # self.prog is "pprl", or for a subcommand's parser, e.g. "pprl hash".
        program, _, command = self.prog.partition(" ")
        example = _example(command or program)
        if message.startswith("the following arguments are required"):
            raise CommandLineError(
                msg.MISSING_ARGUMENTS(
                    command=self.prog,
                    missing=_wording.listed(
                        msg.ARGUMENT_NAMES.get(name.strip(), name.strip())
                        for name in message.split(":", 1)[1].split(",")
                    ),
                    example=example,
                )
            )
        raise CommandLineError(
            msg.COMMAND_LINE_PROBLEM(detail=_wording.supplied(message))
        )


def _build_parser() -> Parser:
    parser = Parser(prog="pprl", add_help=True)
    parser.format_help = _format_help
    parser.add_argument("--version", action="version", version=f"pprl {RELEASE}")
    subparsers = parser.add_subparsers(dest="command")
    commands = {name: subparsers.add_parser(name) for name in _COMMANDS}
    commands["inspect"].add_argument("file", help=msg.HELP_INSPECT_ARGUMENT)
    for name, command in commands.items():
        command.format_help = lambda name=name, command=command: _command_help(
            name, command
        )
    return parser


# The commands that run outside a workspace and are not logged. init makes
# the folder a workspace, or lists its files if it already is one.
# destroy's receipt serves as its log.
_OUTSIDE = ("init", "destroy")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the pprl command line ``argv`` and return the exit code.

    If ``argv`` is not given, use the program's own command line.

    Some command lines run outside a workspace and are not logged: help, the
    version, pprl on its own, the _OUTSIDE commands, and an unknown command
    typed outside a workspace. Every other command runs in the workspace it
    is typed in (see ``in_workspace``). It is logged under its command line
    (see ``command_line``).
    """
    prepare_terminal()
    parser = _build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    argcomplete.autocomplete(parser)
    logged = command_line(parser.prog, argv, _COMMANDS)

    def parsed(marker: Marker | None, bundle: dict | None) -> int:
        return _parse_and_run(parser, argv, marker, bundle, logged)

    if (
        not argv
        or argv[0] in _OUTSIDE
        or any(a in _OPTIONS for a in argv)
        or _unknown_outside(argv[0])
    ):
        return guarded(lambda: parsed(None, None))
    return in_workspace(logged, parsed)


# The options a command line may have. The log shows them as typed.
_OPTIONS = ("-h", "--help", "--version")


def command_line(program: str, argv: Sequence[str], commands: Iterable[str]) -> str:
    """Write a command line the way the log's header shows it.

    The program name, and each word that is one of ``commands`` or _OPTIONS,
    are shown as they are. Any other word (such as a folder or file name) is
    text the user supplied, and is withheld from the log.
    >>> _wording.logged(command_line("pprl", ["inspect", "notes.csv"], ["inspect"]))
    'pprl inspect <withheld>'
    """
    words = [
        word if word in commands or word in _OPTIONS else _wording.supplied(word)
        for word in argv
    ]
    return " ".join([program, *words])


def _unknown_outside(command: str) -> bool:
    """Tell whether ``command`` is unknown and was typed outside a workspace.

    Such a command is refused as unknown, rather than as outside a workspace.
    """
    return (
        command not in _COMMANDS
        and not command.startswith("-")
        and not is_workspace(workspace())
    )


def _parse_and_run(
    parser: Parser,
    argv: list[str],
    marker: Marker | None,
    bundle: dict | None,
    logged: str,
) -> int:
    """Parse the command line, run its command (see ``main``), return the exit code."""
    if argv and not argv[0].startswith("-") and argv[0] not in _COMMANDS:
        return fail(
            msg.UNKNOWN_COMMAND(
                program=parser.prog,
                command=_wording.supplied(argv[0]),
                commands=_wording.listed(_COMMANDS),
            )
        )
    try:
        args = parser.parse(argv)
    except CommandLineError as e:
        return fail(str(e))
    except SystemExit as e:  # --help and --version exit with code 0
        if e.code == 0:
            raise
        return 1
    if not args.command:
        print(colored(parser.format_help()))  # noqa: T201 (the help, as --help shows it)
        return 1
    return _run(args, marker, bundle, logged)


def _run(
    args: argparse.Namespace,
    marker: Marker | None,
    bundle: dict | None,
    command_line: str,
) -> int:
    """Run the pprl command named by the parsed arguments; return the exit code."""
    if args.command in ROLE_COMMANDS:
        if marker is None:  # Never: main runs this only in a workspace.
            raise AssertionError
        return run_role_command(args.command, marker, bundle, workspace())
    if args.command in PROJECT_COMMANDS:
        if marker is None:  # Never: main runs this only in a workspace.
            raise AssertionError
        # The coordinator writes the project's settings, so a problem with
        # them comes with the coordinator's fix.
        with _wording.for_settings_writer():
            return _run_project_command(args, marker, workspace())
    if args.command == "inspect":
        with progress("inspect", ((msg.STEP_READING_FILE, 1),)):
            return run_inspect(args.file)
    if args.command == "destroy":
        return _destroy.destroy()
    if is_workspace(workspace()):
        with progress("init", ((msg.STEP_READING_WORKSPACE, 1),)):
            return _init.show_files(command_line)
    with progress("init", ((msg.STEP_CREATING_WORKSPACE, 1),)):
        return _init.init(command_line)


def _run_project_command(args: argparse.Namespace, marker: Marker, folder: Path) -> int:
    """Run the project command named by the parsed arguments.

    It runs in the workspace ``folder``, whose marker is ``marker``. Return
    the exit code.
    """
    if args.command == "synth":
        with progress("synth", SYNTH_STEPS):
            return synthesize(
                scenario=SYNTH_CONFIG, data_folder=folder, output_folder=folder
            )
    if args.command == "sweep":
        with progress("sweep", _sweep.STEPS):
            return _sweep.sweep(SWEEP_CONFIG)
    if args.command == "bundles":
        with progress("bundles", ((msg.STEP_WRITING_BUNDLES, 1),)):
            return _project.write_bundles(marker, folder)
    if args.command == "secrets":
        with progress("secrets", ((msg.STEP_LOCKING_SECRETS, 1),)):
            return _project.write_secrets(marker, folder)
    if args.command == "guides":
        with progress("guides", ((msg.STEP_WRITING_GUIDES, 1),)):
            return _guides.write_guides(folder)
    with progress("review", ((msg.STEP_READING_REPORT, 1),)):
        return _review.review(marker.project, folder)
