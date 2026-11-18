r"""Read and write pprl files, the format of every file pprl writes for any role.

The file format has its own specification document. In short:

    PPRL/1 hashes file, written by pprl 3.0.0. Do not open or edit it: ...\n
    <zlib stream of: header length, header JSON, then each member's length
     and bytes, in the header's order; each length 8 bytes, big-endian>

The header says what the file is and where it came from:

    {"format": 1, "kind": "hashes", "id": "<random id of this file>",
     "set": "<bundle set id>", "project": "<project id>", "site": "site_a",
     "release": "3.0.0", "created": "2026-11-16T10:15:02-05:00",
     "members": [{"name": "hashes", "type": "csv", "rows": 812,
                  "columns": ["study_id", "hash"]}],
     "inputs": [{"kind": "clean", "id": "<its id>", "site": "site_a",
                 "inputs": [...]}]}

A file holds no checksum, and nothing computed from the secret except the
hashes themselves. zlib's own check, together with the row and column
counts the header gives for each member, catches a damaged file. The
random ids in ``inputs`` trace which files it was made from.

``read`` checks everything the header allows it to check, and raises
PprlFileError with a message for the user. ``write`` writes a file all at
once.
"""

import io
import json
import re
import struct
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, override

import pandas as pd

from . import _messages as msg
from ._io import BLOCK, display_name, for_writing, read_strings_csv, validated_site
from ._shared import (
    HASH_COLUMNS,
    LINKAGE_COLUMNS,
    RELEASE,
    ROLE_KEYS,
    Member,
    UserFacingError,
    is_other_release,
    is_text,
    now,
)
from ._ui import add_work, advance

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from typing import BinaryIO

    from _typeshed import WriteableBuffer

FORMAT = 1
MAGIC = b"PPRL/1 "
FIRST_LINE = (
    "PPRL/1 {kind} file, written by pprl {release}. Do not open or edit it: "
    "run pprl inspect on it to see what it holds.\n"
)
# What each kind of file is called, in file names and in messages.
KINDS = (
    "bundle",
    "private_key",
    "public_key",
    "secret",
    "selected",
    "clean",
    "hashes",
    "tables",
    "linkages",
    "linkage_report",
)
_LENGTH = struct.Struct(">Q")
_REQUIRED_MEMBERS = {
    "bundle": {Member.SETTINGS: ()},
    "private_key": {Member.KEY: ()},
    "public_key": {Member.KEY: ()},
    "secret": {Member.KEY: ()},
    "selected": {Member.COHORT: ()},
    "clean": {Member.PATIENTS: ()},
    "hashes": {Member.HASHES: HASH_COLUMNS},
    "tables": {},
    "linkages": {Member.LINKAGES: LINKAGE_COLUMNS},
    "linkage_report": {Member.QUALITY: ()},
}
# The members written as JSON. All other members are CSV.
_JSON_MEMBERS = {
    "bundle": {Member.SETTINGS},
    "private_key": {Member.KEY},
    "public_key": {Member.KEY},
    "secret": {Member.KEY},
    "linkage_report": {Member.QUALITY},
}
# How many rows of a DataFrame member are written as CSV at a time.
RENDER_ROWS = 50_000
# The header keys every file has. Only a bundle also has "role".
_HEADER_KEYS = frozenset(
    {"format", "kind", "id", "set", "project", "site", "release", "created"}
    | {"members", "inputs"}
)
# The longest header pprl will read. It is far longer than any header pprl
# writes, but short enough that a file declaring a huge header can't fill the
# memory.
MAX_HEADER = 1 << 20
# How deeply a header's ``inputs`` may nest. The longest real chain is 4
# (selected, clean, hashes, linkages).
MAX_LINEAGE = 8
# The longest allowed site name, since the name becomes part of file names.
MAX_SITE = 100
_RELEASE = re.compile(r"[0-9A-Za-z.+!-]{1,64}")


def file_name(kind: str, site: str | None = None) -> str:
    """Return the name of a pprl file of ``kind``, including ``site`` if it is per site.

    >>> file_name("hashes", "site_a"), file_name("linkages")
    ('site_a.hashes.pprl', 'linkages.pprl')
    """
    return f"{site}.{kind}.pprl" if site else f"{kind}.pprl"


class PprlFileError(UserFacingError):
    """Raised for a pprl file that cannot be used."""


@dataclass
class PprlFile:
    """A pprl file's header, and its members by name.

    A CSV member is a DataFrame of strings, and a JSON member is a dict. A
    file read with ``lazy`` (see ``read``) has no members loaded. Use
    ``chunks`` to read a member one part at a time.
    """

    header: dict
    path: Path
    members: dict = field(default_factory=dict)

    def chunks(self, name: str, chunk_rows: int) -> Iterator[pd.DataFrame]:
        """Read the CSV member ``name``, up to ``chunk_rows`` rows at a time.

        Yield DataFrames of strings, and never the whole member at once. A
        member with no rows yields one empty DataFrame. Raise PprlFileError
        if the member does not match the header's description.
        """
        described = self.member(name)
        name = display_name(self.path)
        damaged = PprlFileError(msg.FILE_DAMAGED(name=name))
        if described is None:
            raise damaged
        with Path(self.path).open("rb") as fh:
            try:
                payload = _payload(fh, name)
                for entry in [None, *self.header["members"]]:
                    length = payload.length()
                    if entry is described:
                        break
                    payload.skip(length)
                frames = read_strings_csv(
                    io.BufferedReader(_Part(payload, length), BLOCK),
                    chunksize=chunk_rows,
                )
                rows = yielded = 0
                for frame in frames:
                    if list(frame.columns) != described["columns"]:
                        raise damaged
                    rows, yielded = rows + len(frame), yielded + 1
                    yield frame
            except ValueError, zlib.error, pd.errors.ParserError, EOFError:
                raise damaged from None
        if rows != described["rows"]:
            raise damaged
        if not yielded:
            yield pd.DataFrame(columns=described["columns"], dtype=str)

    def member(self, name: str) -> dict | None:
        """Return the header's description of the member ``name``.

        Return None if the file has no such member.
        """
        return next((m for m in self.header["members"] if m["name"] == name), None)

    @property
    def id(self) -> str:
        return self.header["id"]

    @property
    def lineage(self) -> dict:
        """Describe this file as a later file's ``inputs`` records it, by ids only."""
        return {
            "kind": self.header["kind"],
            "id": self.header["id"],
            "site": self.header["site"],
            "inputs": self.header["inputs"],
        }


@dataclass
class Spooled:
    """A CSV member written to its own file before the pprl file is written.

    This keeps the member from ever being in memory all at once. It records
    the file, the member's rows and its columns.
    """

    path: Path
    rows: int
    columns: list


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def write(
    path: Path | str,
    kind: str,
    members: dict,
    *,
    provenance: dict,
    inputs: Iterable[PprlFile | dict] = (),
    **extra: object,
) -> dict:
    """Write a pprl file of ``kind`` at ``path`` all at once, and return its header.

    Never overwrite an existing file. The header is a dict.

    ``members`` maps each member's name to a DataFrame (written as CSV) or a
    dict (written as JSON), in order. ``provenance`` is the project, bundle
    set and site the file is for, under the keys project, set and site.
    The site is missing or None for a file that belongs to no single site.
    ``inputs`` are the PprlFiles this file was made from, or their lineage
    as a header's ``inputs`` gives it. ``extra`` adds header keys, such as a
    bundle's ``role``.
    """
    described = []
    for name, member in members.items():
        if isinstance(member, pd.DataFrame):
            rows, columns = len(member), [str(c) for c in member.columns]
        elif isinstance(member, Spooled):
            rows, columns = member.rows, list(member.columns)
        else:
            described.append({"name": name, "type": "json"})
            continue
        described.append(
            {"name": name, "type": "csv", "rows": rows, "columns": columns}
        )
    header = {
        "format": FORMAT,
        "kind": kind,
        "id": str(uuid.uuid4()),
        "set": provenance["set"],
        "project": provenance["project"],
        "site": provenance.get("site"),
        "release": RELEASE,
        "created": now().isoformat(timespec="seconds"),
        "members": described,
        "inputs": [f.lineage if isinstance(f, PprlFile) else f for f in inputs],
        **extra,
    }
    add_work(sum(_work(member) for member in members.values()))
    first = FIRST_LINE.format(kind=kind, release=header["release"]).encode("ascii")
    compressor = zlib.compressobj(6)
    with for_writing(path, binary=True) as handle:
        handle.write(first)

        def part(length: int, blocks: Iterable[bytes]) -> None:
            handle.write(compressor.compress(_LENGTH.pack(length)))
            for block in blocks:
                handle.write(compressor.compress(block))

        data = json.dumps(header).encode("utf-8")
        part(len(data), [data])
        for member in members.values():  # One at a time, never all at once
            if isinstance(member, Spooled):
                size = member.path.stat().st_size
                part(size, _counted(_blocks(member.path), member.rows / max(size, 1)))
                continue
            if isinstance(member, pd.DataFrame):
                # A member's length comes before its bytes, so the whole
                # member is turned into CSV before any of it is compressed.
                blocks = _csv_blocks(member)
                size = sum(map(len, blocks))
                part(size, _counted(blocks, len(member) / max(size, 1)))
                del blocks
                continue
            data = json.dumps(member).encode("utf-8")
            part(len(data), [data])
            del data
        handle.write(compressor.flush())
    return header


def _work(member: object) -> int:
    """Count the progress bar units for writing ``member``.

    Each row of a CSV member counts once as it is compressed. A DataFrame's
    rows also count once before that, as they are turned into CSV.
    """
    if isinstance(member, Spooled):
        return member.rows
    return 2 * len(member) if isinstance(member, pd.DataFrame) else 0


def _csv_blocks(frame: pd.DataFrame) -> list:
    """Convert a DataFrame to the bytes of a CSV file, in blocks of rows.

    The progress bar moves between blocks. Joined together, the blocks equal
    what to_csv writes for the whole DataFrame.
    """
    options = {"index": False, "lineterminator": "\n"}
    blocks = [frame.iloc[:0].to_csv(**options).encode("utf-8")]
    for start in range(0, len(frame), RENDER_ROWS):
        rows = frame.iloc[start : start + RENDER_ROWS]
        blocks.append(rows.to_csv(header=False, **options).encode("utf-8"))
        advance(len(rows))
    return blocks


def _counted(blocks: Iterable[bytes], rows_per_byte: float) -> Iterator[bytes]:
    """Yield ``blocks``, advancing the progress bar by each block's rows."""
    for block in blocks:
        yield block
        advance(len(block) * rows_per_byte)


def _blocks(path: Path | str) -> Iterator[bytes]:
    """Read a file's bytes one block at a time."""
    with Path(path).open("rb") as fh:
        while block := fh.read(BLOCK):
            yield block


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def is_pprl_file(path: Path | str) -> bool:
    """Tell whether the file at ``path`` starts the way a pprl file does.

    Only the first bytes are read, however large the file is.
    """
    with Path(path).open("rb") as fh:
        return fh.read(len(MAGIC)) == MAGIC


def read_header(path: Path | str) -> dict:
    """Read only the header of the pprl file at ``path``, or raise PprlFileError."""
    return _unpack(path, display_name(path), lazy=True).header


def read(
    path: Path | str,
    kind: str | None = None,
    *,
    project: str | None = None,
    set_id: str | None = None,
    site: str | None = None,
    check_release: bool = True,
    lazy: bool = False,
    supplied: bool = False,
) -> PprlFile:
    """Read and check the pprl file at ``path``.

    Check that it is a pprl file, complete and as pprl wrote it, with every
    member's rows and columns as the header says. Check that it is from this
    release, unless ``check_release`` is false. For each of ``kind``,
    ``project``, ``set_id`` (the bundle set) and ``site`` that is given,
    check that the file matches it.

    ``lazy`` reads no members, for a file too large to hold in memory. It
    still checks that the file is complete. Each member's rows and columns
    are then checked as ``PprlFile.chunks`` reads it. If the user
    ``supplied`` the file's name, messages mark the name that way.

    Raise PprlFileError with a message for the user that never quotes the
    file's contents. Raise FileNotFoundError if there is no file.
    """
    name = display_name(path, supplied=supplied)
    pprl_file = _unpack(path, name, lazy=lazy)
    header = pprl_file.header
    if kind is not None and header["kind"] != kind:
        if header["kind"] == "bundle":
            raise PprlFileError(msg.FILE_IS_BUNDLE(name=name))
        raise PprlFileError(
            msg.FILE_OTHER_KIND(
                name=name,
                found=msg.FILE_KINDS[header["kind"]],
                expected=msg.FILE_KINDS[kind],
            )
        )
    if check_release and is_other_release(header["release"]):
        raise PprlFileError(
            msg.FILE_OTHER_RELEASE(
                name=name, release=header["release"], version=RELEASE
            )
        )
    if project is not None and header["project"] != project:
        raise PprlFileError(msg.FILE_OTHER_PROJECT(name=name))
    if set_id is not None and header["set"] != set_id:
        raise PprlFileError(msg.FILE_OTHER_SET(name=name))
    if site is not None and header["site"] != site:
        raise PprlFileError(
            msg.FILE_OF_OTHER_SITE(name=name, found=header["site"], site=site)
        )
    return pprl_file


def _unpack(path: Path | str, name: str, *, lazy: bool = False) -> PprlFile:
    """Read the header and members of a pprl file, checking that it is complete.

    If ``lazy``, read only the header. The file is read a block at a time.
    ``name`` is the file's name, for messages.
    """
    damaged = PprlFileError(msg.FILE_DAMAGED(name=name))
    with Path(path).open("rb") as fh:
        payload = _payload(fh, name)
        try:
            length = payload.length()
            if length > MAX_HEADER:
                raise damaged
            header = json.loads(payload.read(length).decode("utf-8"))
            if not _well_formed(header):
                raise damaged
            pprl_file = PprlFile(header, path=Path(path))
            for described in header["members"]:
                length = payload.length()
                if lazy:
                    payload.skip(length)
                    continue
                part = payload.read(length)
                pprl_file.members[described["name"]] = _member(described, part)
            if not payload.at_end():
                raise damaged
        except (
            ValueError,
            EOFError,
            zlib.error,
            UnicodeDecodeError,
            pd.errors.ParserError,
            RecursionError,  # JSON nested too deeply to parse
        ):
            raise damaged from None
    return pprl_file


def _payload(fh: BinaryIO, name: str) -> _Payload:
    """Open the payload of the pprl file ``fh``, which follows its first line.

    The first line is there for people to read. Raise PprlFileError if the
    file ``name`` is not a pprl file.
    """
    if fh.read(len(MAGIC)) != MAGIC:
        raise PprlFileError(msg.NOT_A_PPRL_FILE(name=name))
    fh.readline()
    return _Payload(fh)


class _Payload:
    """A pprl file's payload, decompressed as it is read.

    The payload is a series of parts, each preceded by its length.
    """

    def __init__(self, fh: BinaryIO) -> None:
        self.fh, self.stream, self.buffer = fh, zlib.decompressobj(), bytearray()

    def _fill(self, size: int) -> None:
        """Decompress until ``size`` bytes are buffered, or the file ends.

        Decompress at most one block at a time, however much the file's
        bytes expand.
        """
        while len(self.buffer) < size and not self.stream.eof:
            compressed = self.stream.unconsumed_tail or self.fh.read(BLOCK)
            if not compressed:
                reason = "cut short"
                raise EOFError(reason)
            self.buffer += self.stream.decompress(compressed, BLOCK)

    def read(self, size: int) -> bytes:
        """Read the next ``size`` bytes. Raise EOFError if there are fewer."""
        self._fill(size)
        if len(self.buffer) < size:
            reason = "cut short"
            raise EOFError(reason)
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def length(self) -> int:
        """Read the length of the next part."""
        (length,) = _LENGTH.unpack(self.read(_LENGTH.size))
        return length

    def skip(self, size: int) -> None:
        """Skip the next ``size`` bytes, one block at a time."""
        while size:
            size -= len(self.read(min(size, BLOCK)))

    def at_end(self) -> bool:
        """Tell whether the payload ends here, together with the compressed stream."""
        self._fill(1)
        return not self.buffer and self.stream.eof


class _Part(io.RawIOBase):
    """One part of a payload, ``size`` bytes long, as a readable file.

    pandas reads a member through this in chunks.
    """

    def __init__(self, payload: _Payload, size: int) -> None:
        self.payload, self.left = payload, size

    def readable(self) -> bool:
        return True

    @override
    def readinto(self, buffer: WriteableBuffer, /) -> int:
        view = memoryview(buffer)
        size = min(len(view), self.left, BLOCK)
        if not size:
            return 0
        data = self.payload.read(size)
        view[:size] = data
        self.left -= size
        return size


def _well_formed(header: dict) -> bool:
    """Tell whether a header is one that pprl could have written."""
    if not isinstance(header, dict):
        return False
    bundle = header.get("kind") == "bundle"
    return (
        set(header) == (_HEADER_KEYS | {"role"} if bundle else _HEADER_KEYS)
        and type(header["format"]) is int
        and header["format"] == FORMAT
        and header["kind"] in KINDS
        and all(_is_id(header[key]) for key in ("id", "set", "project"))
        and _is_site(header["site"])
        and isinstance(header["release"], str)
        and _RELEASE.fullmatch(header["release"]) is not None
        and _is_moment(header["created"])
        and (not bundle or _is_role(header["role"]))
        and _are_members(header["members"])
        and _has_its_members(header["kind"], header["members"])
        and _is_lineage(header["inputs"])
    )


def _is_id(value: object) -> bool:
    """Tell whether a value is a UUID written the way pprl writes one.

    >>> _is_id("00000000-0000-4000-8000-000000000000")
    True
    >>> [_is_id(v) for v in ("00000000000040008000000000000000", "x", 1)]
    [False, False, False]
    """
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _is_site(value: object) -> bool:
    """Tell whether a value is valid as a header's site.

    That is a site's name, or None for a file that belongs to no single site.
    """
    if value is None:
        return True
    try:
        site = validated_site(value)
    except ValueError:
        return False
    return len(site) <= MAX_SITE


def _is_moment(value: object) -> bool:
    """Tell whether a value is a time as a header writes it, with its time zone.

    >>> [_is_moment(v) for v in ("2026-11-16T10:15:02-05:00", "2026-11-16T10:15:02", 1)]
    [True, False, False]
    """
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def _is_role(value: object) -> bool:
    """Tell whether a value is a bundle's role."""
    return isinstance(value, str) and value in ROLE_KEYS


def _is_shown_text(value: object) -> bool:
    r"""Tell whether a value is text that a message or a log can show.

    JSON can encode half of a surrogate pair, which no text encoding can
    write.
    >>> [_is_shown_text(v) for v in ("a", "", "\ud800", 1)]
    [True, False, False, False]
    """
    if not is_text(value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _are_members(members: object) -> bool:
    """Tell whether a value is a valid ``members`` list, with each name used once."""
    return (
        isinstance(members, list)
        and all(_is_member(member) for member in members)
        and len({member["name"] for member in members}) == len(members)
    )


def _is_member(member: object) -> bool:
    """Tell whether a value is a valid description of a member.

    It must give the member's name and type. A CSV member must also give its
    rows and columns, since without columns it would have no header row.
    """
    if not isinstance(member, dict) or not _is_shown_text(member.get("name")):
        return False
    if member.get("type") == "json":
        return set(member) == {"name", "type"}
    return (
        member.get("type") == "csv"
        and set(member) == {"name", "type", "rows", "columns"}
        and type(member["rows"]) is int
        and member["rows"] >= 0
        and isinstance(member["columns"], list)
        and len(member["columns"]) > 0
        and all(_is_shown_text(column) for column in member["columns"])
    )


def _has_its_members(kind: str, members: list[dict]) -> bool:
    """Tell whether ``members`` have the types that a file of ``kind`` needs.

    Each member must be well formed, and the file must have the members and
    columns it can't be used without.
    """
    described = {member["name"]: member for member in members}
    as_json = _JSON_MEMBERS.get(kind, ())
    return all(
        (member["type"] == "json") == (member["name"] in as_json) for member in members
    ) and all(
        name in described and set(columns) <= set(described[name].get("columns", ()))
        for name, columns in _REQUIRED_MEMBERS[kind].items()
    )


def _is_lineage(inputs: object, depth: int = 1) -> bool:
    """Tell whether a value is a valid ``inputs`` list, in a header or an input.

    ``depth`` is the current nesting level, which may not exceed MAX_LINEAGE.
    """
    return (
        isinstance(inputs, list)
        and (not inputs or depth <= MAX_LINEAGE)
        and all(_is_input(entry, depth) for entry in inputs)
    )


def _is_input(entry: object, depth: int) -> bool:
    """Tell whether a value is a valid entry in a file's lineage."""
    return (
        isinstance(entry, dict)
        and set(entry) == {"kind", "id", "site", "inputs"}
        and entry["kind"] in KINDS
        and _is_id(entry["id"])
        and _is_site(entry["site"])
        and _is_lineage(entry["inputs"], depth + 1)
    )


def _member(described: dict, part: bytes) -> pd.DataFrame | object:
    """Parse one member, checking it against its description in the header."""
    if described["type"] == "json":
        return json.loads(part.decode("utf-8"))
    df = read_strings_csv(io.BytesIO(part))
    if list(df.columns) != described["columns"] or len(df) != described["rows"]:
        reason = "not as its header describes it"
        raise ValueError(reason)
    return df


def lineage_ids(lineage: list[dict], kind: str) -> dict[str, str]:
    """Find the ids of every file of ``kind`` that a file was made from, by site.

    The search covers all levels of ``inputs``.

    >>> clean = {"kind": "clean", "id": "c", "site": "a", "inputs": []}
    >>> lineage = [{"kind": "hashes", "id": "h", "site": "a", "inputs": [clean]}]
    >>> lineage_ids(lineage, "clean")
    {'a': 'c'}
    """
    found = {}
    for entry in lineage:
        if entry["kind"] == kind:
            found[entry["site"]] = entry["id"]
        found |= lineage_ids(entry["inputs"], kind)
    return found
