"""Build pprl's messages from the templates in the message catalog.

A ``Message`` is a template. Calling it with values for its {placeholders}
gives the text to show: ``msg.WROTE_HASHES(count=1200, site="site_a",
output=...)``. Every template in the catalog is a Message, whatever it is
for (a problem, a result, a label, or a line of the log or of help). This
way every template is filled in the same way, by the same formatter. Text
with nothing to fill in, from a single word to a whole sentence, is a plain
string and is used as it is.

A count is written with its noun as the placeholder's format:
``{count:row(s)}`` gives "1 row" or "1,200 rows". Given a list, the same
format names the items: ``{rows:row(s)}`` gives "row 7" or "rows 7, 9 and
5 more" (see ``counted``). So no message ever shows "row(s)" to a user, and
each message is worded to read correctly with one item or many.

The rest of this module holds what messages share:

- the escalation sentence that ends every fix that escalates
- how values, lists, sizes and times are written
- the marking of text the user supplied, which the terminal shows and the
  log withholds
"""

import contextlib
import numbers
import re
import string
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Iterator

_section = ""
_settings_writer = (
    False  # Whether a command on the project is running (see for_settings_writer)
)


def begin_section(section: str) -> None:
    """Group the messages defined after this call under the heading ``section``.

    The headings organize the generated document that lists every message.
    """
    global _section  # noqa: PLW0603 (tracks the catalog's state while it is defined)
    _section = section


# What a user should do about a problem that only the coordinator can fix, or
# one they can't see how to fix. Every fix that escalates ends with this.
ESCALATE = (
    "Send your log (the .log file in this folder) to the coordinator over a "
    "secure channel."
)

# The kinds of fix (see Message).
ACT, ESCALATES, SETTINGS = "act", "escalate", "settings"


@contextlib.contextmanager
def for_settings_writer() -> Generator[None]:
    """Within the block, show settings problems with the coordinator's fix.

    The commands on the project run inside this block. In them, a problem
    with the project's settings is shown with the fix meant for the
    coordinator, who writes the settings (see Message).
    """
    global _settings_writer  # noqa: PLW0603 (see above)
    before, _settings_writer = _settings_writer, True
    try:
        yield
    finally:
        _settings_writer = before


@dataclass(frozen=True)
class Message:
    """A message template. Call it with its {placeholders} to get the text to show.

    Its ``kind`` says what the fix asks of the user:

    * ``act`` (ACT): something the user can do themselves, described in
      plain words.
    * ``escalate`` (ESCALATES): send the log to the coordinator, after
      first trying what ``fix`` says, if anything.
    * ``settings`` (SETTINGS): a problem with the project's settings, which
      only the coordinator can fix. pprl's users are asked to send the log.
      The commands on the project show ``fix`` instead, because they are run
      by the coordinator, who writes the settings.
    """

    text: str
    fix: str = ""
    kind: str = ACT
    section: str = field(default_factory=lambda: _section, compare=False)

    @property
    def user_fix(self) -> str:
        """Return the fix that a user of pprl is shown."""
        if self.kind == ESCALATES:
            return " ".join(filter(None, (self.fix, ESCALATE)))
        if self.kind == SETTINGS:
            return ESCALATE
        return self.fix

    def __call__(self, **values: object) -> str:
        fix = self.fix if self.kind == SETTINGS and _settings_writer else self.user_fix
        text = _Filled(self._filled(_FORMATTER, fix, values))
        text.bold = self._filled(_BOLD_FORMATTER, fix, values)
        return text

    def _filled(
        self, formatter: string.Formatter, fix: str | None, values: dict
    ) -> str:
        text = formatter.format(self.text, **values)
        if fix:
            text += "\n" + FIX_PREFIX + formatter.format(fix, **values)
        return text


class _Filled(str):
    """A message's text, with ``bold`` holding the same text with counts in bold.

    Used for a result line.
    """

    __slots__ = ("bold",)
    bold: str


BOLD, NORMAL = "\x1b[1m", "\x1b[22m"
# Matches a placeholder format that names what it counts, such as row(s) or
# hash(es).
_NOUN = re.compile(r"[A-Za-z][A-Za-z_ -]*\((s|es)\)")


class _Formatter(string.Formatter):
    """Like str.format, but also accepts a noun as a format (see ``counted``)."""

    def format_field(self, value: object, format_spec: str) -> str:
        if _NOUN.fullmatch(format_spec):
            return counted(value, format_spec)
        return super().format_field(value, format_spec)


class _BoldFormatter(_Formatter):
    """Like _Formatter, but writes counts in bold."""

    def format_field(self, value: object, format_spec: str) -> str:
        text = super().format_field(value, format_spec)
        return f"{BOLD}{text}{NORMAL}" if _NOUN.fullmatch(format_spec) else text


_FORMATTER = _Formatter()
_BOLD_FORMATTER = _BoldFormatter()


FIX_PREFIX = "To fix: "

# A message lists at most this many rows, then says how many more there are.
LISTED_AT_MOST = 20


def template_pattern(template: str, *, named: bool = False) -> str:
    """Make a regular expression that matches text made from a message template.

    Each {placeholder} matches any text. If ``named``, each placeholder
    becomes a group with the placeholder's name.
    >>> pattern = template_pattern("{count:row(s)} of {site}", named=True)
    >>> re.fullmatch(pattern, "3 rows of site_a")["site"]
    'site_a'
    """
    parts = []
    for literal, name, _, _ in string.Formatter().parse(template):
        parts.append(re.escape(literal))
        if name is not None:
            parts.append(f"(?P<{name}>.*?)" if named else ".*?")
    return "".join(parts)


def shown(value: object) -> str:
    """Format a config value the way the user wrote it, for a message.

    >>> shown(None), shown("high"), shown([0.9, 1.5]), shown(True), shown("")
    ('missing', 'high', '[0.9, 1.5]', 'true', 'empty')
    """
    if value is None:
        return "missing"
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return "empty"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(shown(v) for v in value) + "]"
    if isinstance(value, dict):
        return "a block of settings"
    return str(value)


def listed(items: Iterable[object]) -> str:
    """Join items with commas, for a message.

    >>> listed(["name", "dob"])
    'name, dob'
    """
    return ", ".join(str(item) for item in items)


def listed_and(items: Iterable[object]) -> str:
    """Join items with commas and a final "and", for a message.

    >>> listed_and(["patients.csv"]), listed_and(["a", "b", "c"])
    ('patients.csv', 'a, b and c')
    """
    *rest, last = (str(item) for item in items)
    return f"{listed(rest)} and {last}" if rest else last


# Markers around text the user supplied, such as a folder name, a typed
# answer, or the name of a column in an export. The marking is added where the
# text enters a message. The terminal shows the text, but the log (which is
# sent to others) shows WITHHELD in its place.
SUPPLIED_START, SUPPLIED_END = "", ""
SUPPLIED = re.compile(f"{SUPPLIED_START}[^{SUPPLIED_END}]*{SUPPLIED_END}")
WITHHELD = "<withheld>"


def supplied(value: object) -> str:
    """Mark ``value`` as text the user supplied, for use in a message.

    >>> logged(f"Workspace: {supplied('study-2026')}")
    'Workspace: <withheld>'
    >>> shown_text(f"Workspace: {supplied('study-2026')}")
    'Workspace: study-2026'
    """
    text = str(value).replace(SUPPLIED_START, "").replace(SUPPLIED_END, "")
    return f"{SUPPLIED_START}{text}{SUPPLIED_END}"


def supplied_list(items: Iterable[object]) -> str:
    """Join items as ``listed`` does, marking each as supplied by the user.

    >>> logged(supplied_list(["Name", "DOB"]))
    '<withheld>, <withheld>'
    """
    return listed(supplied(item) for item in items)


def logged(text: str) -> str:
    """Return ``text`` as the log writes it, with user-supplied text withheld."""
    return SUPPLIED.sub(WITHHELD, text)


def shown_text(text: str) -> str:
    """Return ``text`` as the terminal shows it, with user-supplied text as typed."""
    return text.replace(SUPPLIED_START, "").replace(SUPPLIED_END, "")


def counted(value: object, noun: str) -> str:
    """Write a count or a list with ``noun`` in the singular or plural.

    >>> counted(1, "row(s)"), counted(1200, "hash(es)")
    ('1 row', '1,200 hashes')
    >>> counted([7], "row(s)"), counted([7, 9], "set-aside row(s)")
    ('row 7', 'set-aside rows 7, 9')
    """
    if isinstance(value, bool | str):
        msg = f"{noun} counts a number or a list, not {value!r}"
        raise TypeError(msg)
    one = re.sub(r"\(e?s\)", "", noun)
    many = re.sub(r"\((e?s)\)", r"\1", noun)
    if isinstance(value, numbers.Integral):  # This includes numpy's integers.
        return f"{int(value):,} {one if value == 1 else many}"
    items = value if isinstance(value, Some) else list(cast("Iterable", value))
    return f"{one if len(items) == 1 else many} {listed_some(items)}"


def counted_up_to(count: int, most: int, noun: str) -> str:
    """Write a count that stopped once it passed ``most``.

    Below the limit, this is the same as ``counted``. Past it, it says "more
    than ``most``".
    >>> counted_up_to(3, 1000, "value(s)"), counted_up_to(1001, 1000, "value(s)")
    ('3 values', 'more than 1,000 values')
    """
    if count > most:
        return f"more than {counted(most, noun)}"
    return counted(count, noun)


# The units for a size: bytes up to 1 KB, then KB, MB and GB.
SIZE_UNITS = ("bytes", "KB", "MB", "GB")
_KILO = 1024  # Bytes in a KB, KB in an MB, and so on


def size(count: float) -> str:
    """Write a number of bytes in a form people can read.

    >>> size(812), size(3500), size(120 * 1024**2)
    ('812 bytes', '3.4 KB', '120.0 MB')
    """
    if count < _KILO:
        return f"{count} {SIZE_UNITS[0]}"
    for unit in SIZE_UNITS[1:-1]:
        count /= _KILO
        if count < _KILO:
            return f"{count:.1f} {unit}"
    return f"{count / _KILO:.1f} {SIZE_UNITS[-1]}"


# The format that every message and the log use for a time.
TIME_FORMAT = "%Y-%m-%d %H:%M:%S %z"


def when(moment: object) -> str:
    """Write a time for a message.

    ``moment`` is a datetime, or a time as a file's header stores it.

    >>> when("2026-11-16T09:15:02-05:00")
    '2026-11-16 09:15:02 -0500'
    """
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    if not isinstance(moment, datetime):
        raise TypeError(moment)
    return moment.strftime(TIME_FORMAT)


def listed_some(items: Iterable[object] | Some) -> str:
    """List up to LISTED_AT_MOST items, then say how many more there are.

    >>> listed_some([3, 7])
    '3, 7'
    >>> listed_some(range(25))
    '0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19 and 5 more'
    """
    if not isinstance(items, Some):
        items = Some.of(items)
    shown = listed(items.first)
    more = len(items) - len(items.first)
    return f"{shown} and {more:,} more" if more > 0 else shown


class Some:
    """Keep the first LISTED_AT_MOST of many items, and count them all.

    This is what a message lists. Items are kept as they are found, however
    many there are (for example, rows of a file with millions of rows).

    >>> rows = Some()
    >>> for row in range(2, 30):
    ...     rows.add(row)
    >>> len(rows), listed_some(rows).endswith("20, 21 and 8 more")
    (28, True)
    """

    def __init__(self) -> None:
        self.first, self.count = [], 0

    @classmethod
    def of(cls, items: Iterable[object]) -> Some:
        some = cls()
        some.extend(items)
        return some

    def add(self, item: object) -> None:
        self.count += 1
        if len(self.first) < LISTED_AT_MOST:
            self.first.append(item)

    def extend(self, items: Iterable[object]) -> None:
        for item in items:
            self.add(item)

    def merge(self, other: Some) -> None:
        """Add the items from ``other``, both those it kept and those it counted."""
        self.extend(other.first)
        self.count += other.count - len(other.first)

    def __len__(self) -> int:
        return self.count

    def __iter__(self) -> Iterator[object]:
        return iter(self.first)
