"""Open, read and write files, and check the file names users give.

This module defines the workspace folder, and every way pprl opens, reads
and writes a file: text, YAML and CSV, and files or folders that are
written completely or not at all. It also checks the names a user may give
a file.
"""

import codecs
import contextlib
import contextvars
import csv
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast, overload

import pandas as pd
import yaml

from . import _messages as msg
from . import _wording
from ._shared import repeated

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
    from typing import IO, TextIO

    import numpy as np

# ---------------------------------------------------------------------------
# The workspace
# ---------------------------------------------------------------------------


def workspace() -> Path:
    """Return the folder that holds every file a user names: the current folder.

    pprl never searches parent folders, so there is never any doubt about
    which folder is in use. pprl checks that the folder is a
    workspace before any command runs.
    """
    return Path.cwd()


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def for_reading(
    file: Path | str, encoding: str = "utf_8", newline: str | None = None
) -> Generator[TextIO]:
    """Open a text file for reading, as UTF-8 unless ``encoding`` says otherwise.

    ``newline`` is passed to open.

    All reading goes through this single function, so encoding, error
    handling, and any future auditing or logging hooks can be changed here
    without updating every caller.
    """
    with Path(file).open(encoding=encoding, newline=newline) as fh:
        yield fh


def for_appending(file: Path | str, newline: str | None = None) -> TextIO:
    r"""Open a text file for appending as UTF-8, creating it if needed.

    Examples are the log (such as site_a.log), which every run adds a
    section to, and a command's working file. ``newline`` is passed to open.
    Pass "\n" for CSV, so it is written the same way on any computer.
    """
    return Path(file).open(mode="a", encoding="utf_8", newline=newline)


def read_text(file: Path | str) -> str:
    r"""Read a whole text file as UTF-8, converting any line endings to \n.

    This ignores the computer's own default encoding. Every text file that
    pprl and the tests read goes through this function or ``for_reading``.
    """
    with for_reading(file) as fh:
        return fh.read()


def write_text(file: Path | str, text: str, *, replace: bool = False) -> None:
    """Write ``text`` to ``file`` as UTF-8, all at once (see ``for_writing``).

    The file must be new, unless ``replace`` is true. Every text file that
    pprl and the tests write in one go goes through this function.
    """
    with for_writing(file, replace=replace) as fh:
        fh.write(text)


def _must_be_new(path: Path) -> None:
    """Raise FileExistsError, with a message, if ``path`` already exists.

    ``path`` may be a file or a folder.
    """
    if path.exists():
        raise FileExistsError(msg.OUTPUT_EXISTS(name=display_name(path)))


# The files of the for_writing_together block in progress, if any. Each file
# maps to the temp file that for_writing wrote it to, or None until it has.
_together: contextvars.ContextVar[dict[Path, Path | None] | None] = (
    contextvars.ContextVar("together", default=None)
)


@contextlib.contextmanager
def for_writing(
    file: Path | str, *, binary: bool = False, replace: bool = False
) -> Generator[IO]:
    """Open a file for writing, as UTF-8 text or, with ``binary``, as bytes.

    The data goes to a temp file, which is renamed atomically on success.
    Inside a for_writing_together block, the rename waits until the block
    succeeds. On failure, the temp file is removed. An existing file is never
    replaced unless ``replace`` is true; otherwise FileExistsError is raised.
    """
    file = Path(file)
    if not replace:
        _must_be_new(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=".tmp.")
    tmp = Path(tmp)
    try:
        with (
            os.fdopen(fd, "wb")
            if binary
            else os.fdopen(fd, "w", encoding="utf_8", newline="\n")
        ) as fh:
            yield fh
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    staged = _together.get()
    if staged is not None and file.absolute() in staged:
        staged[file.absolute()] = tmp
    else:
        tmp.replace(file)


@contextlib.contextmanager
def for_writing_together(*files: Path | str) -> Generator[None]:
    """Write several new files, so that either all of them are written or none.

    Inside the block, write each file with for_writing as usual. Each file
    stays in its temp file until the block succeeds, and only then are they
    all moved into place. This way a command never leaves only some of its
    outputs. Raise FileExistsError before the block starts if any of the
    files already exists.
    """
    for file in files:
        _must_be_new(Path(file))
    staged = dict.fromkeys(Path(f).absolute() for f in files)
    token = _together.set(staged)
    try:
        yield
    except BaseException:
        for tmp in staged.values():
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        raise
    finally:
        _together.reset(token)
    for file, tmp in staged.items():
        if tmp is not None:
            tmp.replace(file)


@contextlib.contextmanager
def scratch(folder: Path | str) -> Generator[Path]:
    """Make a new folder for a command's working files, inside ``folder``.

    ``folder`` is the workspace, which already holds the same data. The
    working folder and everything in it are removed when the block ends,
    however it ends. A run that is killed leaves it behind as .tmp.*, which
    pprl destroy deletes with everything else.
    """
    with tempfile.TemporaryDirectory(
        prefix=".tmp.", dir=folder, ignore_cleanup_errors=True
    ) as path:
        yield Path(path)


@contextlib.contextmanager
def for_writing_folder(folder: Path) -> Generator[Path]:
    """Write a new folder all at once.

    Yield a temporary path to build the folder at. Move the folder into place
    only if the block succeeds. A failure leaves no folder, so a command
    never leaves half of its output.
    """
    folder = Path(folder)
    _must_be_new(folder)
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".tmp.", dir=folder.parent) as staging:
        staged = Path(staging) / folder.name
        yield staged
        staged.replace(folder)


class YamlParseError(Exception):
    """Raised for a YAML file that can't be parsed, near ``line`` and ``column``.

    ``line`` and ``column`` count from 1.
    """

    def __init__(self, line: int | str, column: str) -> None:
        super().__init__(line, column)
        self.line, self.column = line, column


class _Loader(yaml.SafeLoader):
    """YAML's safe loader, changed for impossible dates and to follow YAML 1.2.

    A date that does not exist, such as 2026-02-30, is kept as text instead
    of stopping the parser, so that the setting's own check can report it.
    As in YAML 1.2, only true and false are booleans. So `on` (a key in
    found_in rules), `yes`, `no` and `off` stay text. Numbers are never read
    in base 60, so a time such as 11:00 stays text too.
    """

    def construct_yaml_timestamp(self, node: yaml.ScalarNode) -> object:
        try:
            return super().construct_yaml_timestamp(node)
        except ValueError:
            return self.construct_scalar(node)


_Loader.add_constructor("tag:yaml.org,2002:timestamp", _Loader.construct_yaml_timestamp)
_BOOL, _INT = "tag:yaml.org,2002:bool", "tag:yaml.org,2002:int"
_Loader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag not in (_BOOL, _INT)]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    _BOOL, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)
_Loader.add_implicit_resolver(
    _INT,
    re.compile(r"^[-+]?(?:0b[0-1_]+|0[0-7_]+|0|[1-9][0-9_]*|0x[0-9a-fA-F_]+)$"),
    list("-+0123456789"),
)


def load_yaml(path: Path | str) -> object:
    """Read the contents of a YAML file. Return None if it is empty.

    Raise YamlParseError if it can't be parsed. The parser's own message is
    left out, because it can quote the file, which may hold the secret.
    """
    with for_reading(path) as handle:
        try:
            return yaml.load(handle, Loader=_Loader)  # noqa: S506 (_Loader is a SafeLoader)
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None) or getattr(
                error, "context_mark", None
            )
            if mark is None:
                unknown = "?"
                raise YamlParseError(unknown, unknown) from None
            raise YamlParseError(mark.line + 1, mark.column + 1) from None


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


# The allowed characters in a site or table name, since the name becomes part
# of file names.
PLAIN_NAME = re.compile(r"[A-Za-z0-9_]+")


def validated_site(site: object) -> str:
    """Ensure that a site name is strictly formatted, since it is part of file names.

    Examples
    --------
        >>> validated_site("site_a")
        'site_a'
        >>> validated_site("Hospital1")
        'Hospital1'
        >>> validated_site("has spaces")  # doctest: +NORMALIZE_WHITESPACE
        Traceback (most recent call last):
            ...
        ValueError: The site name "has spaces" cannot be used: a site name may
        contain only letters, digits and underscores.
        To fix: Send your log (the .log file in this folder) to the coordinator
        over a secure channel.
    """
    if isinstance(site, str) and PLAIN_NAME.fullmatch(site):
        return site
    raise ValueError(msg.INVALID_SITE(site=_wording.shown(site)))


def validated_name(
    file_name: object,
    *,
    key: str | None = None,
    what: str | None = None,
    supplied: bool = False,
) -> str:
    """Ensure that a file name stays inside the folder it is resolved against.

    ``key`` is the setting that holds the name, such as "patients". If there
    is no setting, ``what`` names the file for messages, such as "The config
    file name". Absolute paths are rejected, and so are relative paths whose
    ``..`` parts lead out of the folder. A ``..`` that stays inside is
    allowed. If the user ``supplied`` the name, messages mark it that way,
    so that the log withholds it.

    Examples
    --------
        >>> validated_name("site_a/patients.csv", key="patients")
        'site_a/patients.csv'
        >>> validated_name("site_a/../patients.csv", key="patients")
        'site_a/../patients.csv'
        >>> validated_name("../patients.csv", key="patients")
        ... # doctest: +NORMALIZE_WHITESPACE
        Traceback (most recent call last):
            ...
        ValueError: `patients` is outside the workspace (../patients.csv), and
        pprl reads and writes files only inside it.
        To fix: Move the file into the workspace, and give its name there, such
        as site_a/patients.csv.
    """
    if not isinstance(file_name, str) or not file_name:
        raise TypeError(_name_problem(file_name, key, what))

    what = f"`{key}`" if key else what
    name = _named(file_name, supplied=supplied)
    if Path(file_name).is_absolute() or Path(os.path.normpath(file_name)).parts[:1] == (
        os.pardir,
    ):
        raise ValueError(msg.NAME_OUTSIDE(what=what, name=name))

    return file_name


def _name_problem(file_name: object, key: str | None, what: str | None) -> str:
    """Return the message for a file name that is missing or not a valid name.

    The name is the value of the setting ``key``, or the name that ``what``
    describes (see validated_name).
    """
    if file_name is None or file_name == "":
        return msg.CONFIG_MISSING_KEY(key=key or "file name")
    return msg.CONFIG_KEY_NOT_A_NAME(key=key or what, value=_wording.shown(file_name))


def validated_file_path(
    key: str,
    kind: str,
    file_name: object,
    file_directory: Path | str,
    *,
    supplied: bool = False,
) -> Path:
    """Build, validate, and return the path to an existing file.

    ``key`` is the config setting that names the file, and ``kind`` says what
    the file is (msg.Kind). Both are used in messages. If the user
    ``supplied`` the name, messages mark it that way.
    """
    file_path = Path(file_directory) / validated_name(
        file_name, key=key, supplied=supplied
    )

    if not file_path.is_file():
        raise FileNotFoundError(
            msg.FILE_NOT_FOUND(what=kind, name=_named(file_name, supplied=supplied))
        )

    return file_path


def validated_out_path(key: str, file_name: object, file_directory: Path | str) -> Path:
    """Build, validate, and return the path for a new export file or folder."""
    file_path = Path(file_directory) / validated_name(file_name, key=key)
    _must_be_new(file_path)
    return file_path


def display_name(path: Path | str, *, supplied: bool = False) -> str:
    """Return a file's name as the user knows it.

    The name is relative to the workspace if the file is inside it. If the
    user ``supplied`` the name, as for the file pprl inspect reads, it is
    marked that way, so that the log withholds it.

    Messages never show a full path.
    """
    path = Path(path)
    try:
        name = path.resolve().relative_to(workspace().resolve()).as_posix()
    except ValueError:
        name = path.name
    return _named(name, supplied=supplied)


def _named(name: object, *, supplied: bool) -> str:
    """Return a file's ``name`` for a message, marked if the user supplied it."""
    return _wording.supplied(name) if supplied else str(name)


def resolve_in_workspace(path: Path | str) -> Path:
    """Resolve a filename or relative path against the workspace.

    Absolute paths are returned unchanged. This is intentional: internal
    callers (tests and sweep) pass absolute paths to temporary folders
    outside the workspace. The CLI layer makes sure that a path a user
    supplies never reaches this function as an absolute path.
    """
    p = Path(path)
    if p.is_absolute():
        return p
    return workspace() / p


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


# The header is row 1 of a CSV file, as a spreadsheet numbers it. So the
# DataFrame row with index i is row i + FIRST_DATA_ROW.
FIRST_DATA_ROW = 2


def spreadsheet_rows(index: Iterable[int]) -> list[int]:
    """Return the row numbers a spreadsheet shows for DataFrame index labels.

    >>> spreadsheet_rows([0, 1, 5])
    [2, 3, 7]
    """
    return [i + FIRST_DATA_ROW for i in index]


def read_csv_frame(file_path: Path | str) -> pd.DataFrame:
    """Read a CSV file into a DataFrame with every column as strings.

    Reading is forgiving about how the file was saved. It accepts a
    byte-order mark (which Excel's "CSV UTF-8" writes), Windows or Mac line
    endings, and blank lines at the end. It is strict about the contents:
    every row must have as many values as the header, and every column must
    have its own name. Values are kept exactly as written.

    Raise ValueError with a message for the user if the file can't be read.
    The message never quotes the file's contents.
    """
    df, _ = read_csv_converting(file_path, convert=False)
    return df


def read_csv_converting(
    file_path: Path | str, *, convert: bool = True, supplied: bool = False
) -> tuple[pd.DataFrame, bool]:
    """Read a file as ``read_csv_frame`` does, and say if it was converted to UTF-8.

    With ``convert``, a file that is not UTF-8 is read as Windows text
    (Windows-1252, the encoding of Excel's plain "CSV" on Windows). pprl
    clean then writes it out as UTF-8. If the user ``supplied`` the file's
    name, messages mark it that way (see display_name).
    """
    chunks, converted = csv_chunks(file_path, convert=convert, supplied=supplied)
    return pd.concat(list(chunks)), converted


# How many rows each chunk of a CSV file holds (see csv_chunks).
CHUNK_ROWS = 200_000
# How many bytes of a file are read or written at a time. This applies when
# checking how a CSV file was saved, and when reading or writing pprl files.
BLOCK = 1 << 20


def csv_chunks(
    file_path: Path | str,
    chunk_rows: int = CHUNK_ROWS,
    *,
    convert: bool = False,
    supplied: bool = False,
    on_read: Callable[[int], None] | None = None,
) -> tuple[Iterator[pd.DataFrame], bool]:
    """Read a CSV file's rows as DataFrames of up to ``chunk_rows`` rows each.

    Yield at least one DataFrame, even an empty one for a file with only a
    header. Each DataFrame is indexed by the rows' positions in the file.
    Also report whether the file was converted to UTF-8 (see
    read_csv_converting). The whole file is never in memory at once.

    The checks are the same as read_csv_frame's, in the same order and with
    the same messages. How the file was saved is checked at once, raising
    ValueError. Rows are checked as they are read. ValueError is raised as
    soon as a problem is known, after the chunks before it have been
    yielded. Callers use none of the chunks until the last one has been read
    without a problem.

    ``on_read``, if given, is called with each chunk and told how many bytes
    were read since the last call.
    """
    name = display_name(file_path, supplied=supplied)
    encoding = _encoding(file_path, name, convert=convert)
    chunks = _chunks(file_path, encoding, name, chunk_rows, on_read)
    return chunks, encoding == "cp1252"


def _encoding(path: Path | str, name: str, *, convert: bool) -> str:
    """Find the encoding to read the file at ``path`` with.

    The file must hold text with no NUL byte, encoded as UTF-8 (or, with
    ``convert``, as Windows text). Otherwise raise ValueError with a message.
    """
    if _is_utf8(path, name):
        return "utf-8-sig"
    if convert and _is_cp1252(path):
        return "cp1252"
    raise ValueError(msg.FILE_NOT_UTF8(name=name))


def _is_utf8(path: Path | str, name: str) -> bool:
    """Tell whether the file at ``path`` is UTF-8 text.

    Raise ValueError with a message if it holds a NUL byte or no text at all.
    """
    utf8, has_text, is_utf8 = codecs.getincrementaldecoder("utf-8-sig")(), False, True
    with Path(path).open("rb") as fh:
        while block := fh.read(BLOCK):
            if b"\x00" in block:  # UTF-16 ("Unicode text"), or not text at all
                raise ValueError(msg.FILE_NOT_UTF8(name=name))
            has_text = has_text or bool(block.strip())
            if is_utf8:
                try:
                    utf8.decode(block)
                except UnicodeDecodeError:
                    is_utf8 = False
    if not has_text:
        raise ValueError(msg.FILE_EMPTY(name=name))
    try:
        utf8.decode(b"", final=True)
    except UnicodeDecodeError:
        is_utf8 = False
    return is_utf8


def _is_cp1252(path: Path | str) -> bool:
    """Tell whether the file at ``path`` can be read as Windows text (cp1252)."""
    with Path(path).open("rb") as fh:
        try:
            while block := fh.read(BLOCK):
                block.decode("cp1252")
        except UnicodeDecodeError:
            return False
    return True


def _chunks(
    path: Path | str,
    encoding: str,
    name: str,
    chunk_rows: int,
    on_read: Callable[[int], None] | None,
) -> Iterator[pd.DataFrame]:
    """Read the rows of a CSV file in ``encoding``, in chunks.

    The rows are checked for the structure of a table (see csv_chunks).
    """
    with for_reading(path, encoding=encoding, newline="") as fh:
        reader = csv.reader(fh, strict=True)
        told = 0
        try:
            for chunk in _table(reader, name, chunk_rows):
                if on_read is not None:
                    position = fh.buffer.tell()
                    on_read(position - told)
                    told = position
                yield chunk
        except csv.Error:
            raise ValueError(
                msg.FILE_MALFORMED(name=name, near=f" (near line {reader.line_num})")
            ) from None


def _table(
    reader: Iterator[list[str]], name: str, chunk_rows: int
) -> Iterator[pd.DataFrame]:
    """Turn the rows from ``reader`` into chunks of DataFrames.

    Some problems raise ValueError only once every row has been read: a
    problem with the header, a blank row before the last row, or a row with
    the wrong number of values. That way a quote left open anywhere
    (csv.Error) is always reported first, as it always has been.
    """
    header = _header(reader, name)
    blank, pending, ragged = _wording.Some(), _wording.Some(), _wording.Some()
    rows, start = [], 0
    for number, row in enumerate(reader, FIRST_DATA_ROW):
        if _is_blank(row):  # Only a problem if a row with values follows
            pending.add(number)
            continue
        if len(pending):
            blank.merge(pending)
            pending = _wording.Some()
        if len(row) != len(header):
            ragged.add(number)
            continue
        rows.append(row)
        if len(rows) == chunk_rows:
            yield _frame(rows, header, start)
            rows, start = [], start + len(rows)
    yield _frame(rows, header, start)
    if len(blank):
        raise ValueError(msg.BLANK_ROWS(name=name, rows=blank))
    if len(ragged):
        raise ValueError(
            msg.RAGGED_ROWS(
                name=name, count=len(ragged), columns=len(header), rows=ragged
            )
        )


def _header(reader: Iterator[list[str]], name: str) -> list[str]:
    """Read the header, which is the first row from ``reader``.

    A problem with the header raises ValueError only once every row has been
    read (see _table).
    """
    header = next(reader, None)
    problem = None
    if header is None or _is_blank(header):
        problem = msg.BLANK_ROWS(name=name, rows=[1])
    else:
        try:
            _check_header(header, name)
        except ValueError as e:
            problem = str(e)
    if problem or header is None:
        for _ in reader:  # Read every row, to catch a quote left open.
            pass
        raise ValueError(problem)
    return header


def _frame(rows: list[list[str]], header: list[str], start: int) -> pd.DataFrame:
    """Return a CSV file's rows as a DataFrame of strings, indexed by position."""
    return pd.DataFrame(
        rows, columns=header, dtype=str, index=range(start, start + len(rows))
    )


def _is_blank(row: list[str]) -> bool:
    return not row or (len(row) == 1 and not row[0].strip())


def _check_header(header: list[str], name: str) -> None:
    """Check that every column has its own name, written exactly.

    A column's name is text the user supplied, which the log withholds.
    """
    if len(header) == 1 and re.search(r"[;\t]", header[0]):
        raise ValueError(msg.WRONG_SEPARATOR(name=name))
    unnamed = sum(1 for column in header if not column.strip())
    if unnamed:
        raise ValueError(msg.UNNAMED_COLUMNS(name=name, count=unnamed))
    spaced = [column for column in header if column != column.strip()]
    if spaced:
        raise ValueError(
            msg.SPACED_COLUMNS(
                name=name,
                columns=[_wording.supplied(f'"{column}"') for column in spaced],
            )
        )
    if columns := repeated(header):
        raise ValueError(
            msg.DUPLICATE_COLUMNS(name=name, columns=_wording.supplied_list(columns))
        )


class Buckets:
    """A table's rows, split into ``count`` working files (buckets) in ``folder``.

    Buckets let a command handle more data than fits in memory, by holding
    one bucket at a time. select, pack and merge put each row in the bucket
    for its patient or person. This keeps each patient's rows together, in
    the same bucket number in every table.

    Each bucket is a CSV file of ``columns`` without a header. It is created
    when the first row is appended to it. ``rows`` counts every row appended.
    """

    def __init__(self, folder: Path, name: str, count: int, columns: list[str]) -> None:
        self.paths = [Path(folder) / f"{name}.{b}.csv" for b in range(count)]
        self.columns = list(columns)
        self.rows = 0

    def __len__(self) -> int:
        return len(self.paths)

    def __iter__(self) -> Iterator[pd.DataFrame]:
        """Yield each bucket's rows in turn (see ``read``)."""
        return map(self.read, range(len(self)))

    def append(self, rows: pd.DataFrame, of: pd.Series | np.ndarray | int) -> None:
        """Append ``rows`` to the end of their buckets.

        ``rows`` is a DataFrame that includes the buckets' columns. ``of`` gives
        each row's bucket, in a form DataFrame.groupby accepts, or a single
        bucket number for all of them.
        """
        parts = [(of, rows)] if isinstance(of, int) else rows.groupby(of)
        for b, part in parts:
            if len(part):
                path = self.paths[cast("int", b)]  # groupby's keys are buckets
                with for_appending(path, newline="\n") as handle:
                    part.to_csv(
                        handle,
                        columns=self.columns,
                        header=False,
                        index=False,
                        lineterminator="\n",
                    )
                self.rows += len(part)

    def read(self, b: int) -> pd.DataFrame:
        """Read the rows of bucket ``b`` as a DataFrame of strings.

        The DataFrame has the buckets' columns. It is empty if no row was
        appended to the bucket.
        """
        if not self.paths[b].exists():
            return pd.DataFrame(columns=self.columns, dtype=str)
        return read_strings_csv(self.paths[b], header=None, names=self.columns)

    def spool(
        self,
        path: Path | str,
        columns: list[str] | None = None,
        each: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
        on_rows: Callable[[int], None] | None = None,
    ) -> int:
        """Append every non-empty bucket to the CSV file ``path``, in turn.

        Write a header of ``columns`` (the buckets' own columns by default).
        Write each bucket's rows as they are, or, if ``each`` is given, the
        rows that ``each(rows)`` returns. Return how many rows were written.
        ``on_rows``, if given, is called with each bucket's row count as it
        is written.
        """
        columns = self.columns if columns is None else columns
        written = 0
        with for_appending(path, newline="\n") as handle:
            handle.write(
                pd.DataFrame(columns=columns).to_csv(index=False, lineterminator="\n")
            )
            for b, bucket in enumerate(self.paths):
                if not bucket.exists():
                    continue
                if each is None:
                    with for_reading(bucket, newline="") as part:
                        text = part.read()
                    handle.write(text)
                    if on_rows is not None:
                        on_rows(text.count("\n"))
                    continue
                rows = each(self.read(b)).reindex(columns=columns)
                rows.to_csv(handle, header=False, index=False, lineterminator="\n")
                written += len(rows)
        return self.rows if each is None else written


@overload
def read_strings_csv(
    source: Path | str | IO, *, header: int | None = 0, names: list[str] | None = None
) -> pd.DataFrame: ...
@overload
def read_strings_csv(
    source: Path | str | IO,
    *,
    header: int | None = 0,
    names: list[str] | None = None,
    chunksize: int,
) -> Iterator[pd.DataFrame]: ...
def read_strings_csv(
    source: Path | str | IO,
    *,
    header: int | None = 0,
    names: list[str] | None = None,
    chunksize: int | None = None,
) -> pd.DataFrame | Iterator[pd.DataFrame]:
    """Read a CSV file that pprl wrote, as a DataFrame of strings.

    Each value is kept exactly as written; an empty value is "", never NaN.
    ``header`` and ``names`` are passed to pandas.read_csv. With
    ``chunksize``, the rows are read that many at a time, as DataFrames.
    """
    return pd.read_csv(
        source,
        dtype=str,
        keep_default_na=False,
        header=header,
        names=names,
        chunksize=chunksize,
    )


def write_linkages(
    path: Path | str, rows: Iterable[Sequence], columns: Sequence[str]
) -> None:
    """Write a CSV file of linked records, one row per record, under ``columns``.

    Synth uses this for its true linkage, with each site's id where pprl
    link writes a study_id.
    """
    with for_writing(path) as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)


def write_csv_frame(df: pd.DataFrame, path: Path | str, *, index: bool = False) -> None:
    """Write a DataFrame to a new CSV file. Fail if the file already exists.

    The default index=False suits all file exports. Internal uses that need
    the index (such as feeding clkhash) call df.to_csv directly.
    """
    with for_writing(path) as fh:
        df.to_csv(fh, index=index, lineterminator="\n")
