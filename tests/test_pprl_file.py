"""Test pprl file headers.

Only valid headers are written, and invalid headers are handled without
crashing.
"""

import copy
import io
import json
import os
import shutil
import struct
import tracemalloc
import zlib
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from pprl import _messages as msg
from pprl import _pprl_file
from pprl._io import BLOCK, write_text
from pprl._pprl_file import FIRST_LINE, KINDS, MAX_HEADER, MAX_LINEAGE, PprlFileError
from pprl._shared import Member
from tests.helpers import (
    SITE_TABLES,
    SITES,
    coordinator_workspace,
    make_workspace,
    provenance,
    run_in,
    shows,
    write_again,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence
    from pathlib import Path

    from pprl._pprl_file import _Payload

pytestmark = pytest.mark.reliability

LENGTH = struct.Struct(">Q")
ID = "11111111-1111-4111-8111-111111111111"
LONG = "x" * 10_000
# The value each header key is set to, keyed by the name of the test case.
VALUES = {
    "none": None,
    "true": True,
    "zero": 0,
    "negative": -1,
    "empty": "",
    "no_items": [],
    "no_keys": {},
    "long": LONG,
}
HEADER_KEYS = (
    "format",
    "kind",
    "id",
    "set",
    "project",
    "site",
    "release",
    "created",
    "members",
    "inputs",
)
CSV_MEMBER_KEYS = ("name", "type", "rows", "columns")
JSON_MEMBER_KEYS = ("name", "type")
INPUT_KEYS = ("kind", "id", "site", "inputs")
# Values that these keys accept, even though they appear in VALUES above.
ALLOWED = {
    "header": {("site", "none"), ("inputs", "no_items")},
    "csv member": {("rows", "zero")},
    "json member": set(),
    "input": {("site", "none"), ("inputs", "no_items")},
}


def _valid(folder: Path, kind: str = "linkages") -> tuple[dict, list[bytes]]:
    """Make the header and member bytes of a valid file of ``kind``.

    The file is as pprl writes it, with an input: a linkage file, with a CSV
    member; a linkage report, with a JSON member; or a bundle.
    """
    path = folder / "valid.pprl"
    source = {
        "kind": "clean",
        "id": ID,
        "site": "site_a",
        "inputs": [],
    }
    extra = {}
    if kind == "bundle":
        members = {Member.SETTINGS: {"name": "Test project"}}
        extra = {"role": "site"}
    elif kind == "linkage_report":
        members = {Member.QUALITY: {"a": 1}}
    else:
        members = {
            Member.LINKAGES: pd.DataFrame(
                {"group_id": ["1"], "site": ["a"], "study_id": ["1"]}
            ),
        }
    _pprl_file.write(
        path, kind, members, provenance=provenance("site_a"), inputs=[source], **extra
    )
    _first, _newline, compressed = path.read_bytes().partition(b"\n")
    data = zlib.decompress(compressed)
    parts = []
    while data:
        (length,) = LENGTH.unpack(data[: LENGTH.size])
        parts.append(data[LENGTH.size : LENGTH.size + length])
        data = data[LENGTH.size + length :]
    path.unlink()
    return json.loads(parts[0]), parts[1:]


def _framed(path: Path, header: dict | bytes, parts: Iterable[bytes]) -> Path:
    """Write ``header`` and ``parts`` at ``path``, framed the way pprl frames a file.

    ``header`` is either bytes, or a value to write as JSON. The framing
    matches the way pprl writes one.
    """
    data = header if isinstance(header, bytes) else json.dumps(header).encode("utf-8")
    body = b"".join(LENGTH.pack(len(part)) + part for part in (data, *parts))
    first = FIRST_LINE.format(kind="hashes", release="3.0.0").encode("ascii")
    path.write_bytes(first + zlib.compress(body))
    return path


def _readers(path: Path) -> tuple[Callable[[], object], ...]:
    """Return each way of reading the file at ``path``, as a no-argument function."""
    return (
        lambda: _pprl_file.read(path),
        lambda: _pprl_file.read(path, lazy=True),
        lambda: _pprl_file.read_header(path),
    )


def _assert_damaged(path: Path) -> None:
    """Assert that every way of reading the file at ``path`` says it is damaged."""
    for reader in _readers(path):
        with pytest.raises(PprlFileError) as problem:
            reader()
        assert shows(msg.FILE_DAMAGED, str(problem.value))


def _assert_reads(path: Path) -> None:
    """Assert that every way of reading the file at ``path`` succeeds."""
    for reader in _readers(path):
        reader()


def _removed(key: str, where: Callable[[dict], dict]) -> Callable[[dict], None]:
    def change(header: dict) -> None:
        del where(header)[key]

    return change


def _set(
    key: str | int, value: object, where: Callable[[dict], dict]
) -> Callable[[dict], None]:
    def change(header: dict) -> None:
        where(header)[key] = copy.deepcopy(value)

    return change


def _header(header: dict) -> dict:
    return header


def _csv_member(header: dict) -> dict:
    return header["members"][0]


def _json_member(header: dict) -> dict:
    """Return a linkage report's JSON member (see BROKEN_JSON)."""
    return header["members"][0]


def _input(header: dict) -> dict:
    return header["inputs"][0]


def _broken_keys(
    what: str, keys: Iterable[str], where: Callable[[dict], dict]
) -> Iterator[tuple[str, Callable[[dict], None]]]:
    """Make test cases that remove each of ``keys``, or set it to each of VALUES.

    Values in ALLOWED are skipped. Each case is (test id, change to make to
    a header).
    """
    for key in keys:
        yield f"{what} no_{key}", _removed(key, where)
        for name, value in VALUES.items():
            if (key, name) not in ALLOWED[what]:
                yield f"{what} {key}={name}", _set(key, value, where)


def _nested(depth: int) -> list:
    """Make an ``inputs`` list nested ``depth`` levels deep, one input per level."""
    inputs = []
    for _ in range(depth):
        inputs = [{"kind": "clean", "id": ID, "site": None, "inputs": inputs}]
    return inputs


def _duplicate_member(header: dict) -> None:
    header["members"].append(copy.deepcopy(header["members"][0]))


BROKEN = {
    **dict(_broken_keys("header", HEADER_KEYS, _header)),
    **dict(_broken_keys("csv member", CSV_MEMBER_KEYS, _csv_member)),
    **dict(_broken_keys("input", INPUT_KEYS, _input)),
    "an unknown key": _set("more", 1, _header),
    "a role, not a bundle": _set("role", "site", _header),
    "a bundle without a role": _set("kind", "bundle", _header),
    "an unknown kind": _set("kind", "other", _header),
    "format true": _set("format", value=True, where=_header),
    "format float": _set("format", 1.0, _header),
    "format 2": _set("format", 2, _header),
    "id without dashes": _set("id", ID.replace("-", ""), _header),
    "id in capitals": _set("id", "ABCDEF12-1111-4111-8111-111111111111", _header),
    "id in braces": _set("id", "{" + ID + "}", _header),
    "set not an id": _set("set", "other", _header),
    "project not an id": _set("project", "other", _header),
    "site with a space": _set("site", "site a", _header),
    "release with a space": _set("release", "3.0 0", _header),
    "release too long": _set("release", "3" * 65, _header),
    "release not ASCII": _set("release", "3.0.é", _header),
    "created without a time zone": _set("created", "2026-11-16T10:15:02", _header),
    "created only a date": _set("created", "2026-11-16", _header),
    "created not a time": _set("created", "yesterday", _header),
    "members not a list": _set("members", {"a": 1}, _header),
    "a member not a dict": _set(0, "hashes", lambda h: h["members"]),
    "a CSV member with more": _set("more", 1, _csv_member),
    "rows true": _set("rows", value=True, where=_csv_member),
    "rows float": _set("rows", 1.0, _csv_member),
    "columns not text": _set("columns", ["group_id", "site", 1], _csv_member),
    "columns blank": _set("columns", ["group_id", "site", " "], _csv_member),
    "a column of half a character": _set("columns", ["\ud800"], _csv_member),
    "a name of half a character": _set("name", "\ud800", _csv_member),
    "duplicate member names": _duplicate_member,
    "an input not a dict": _set("inputs", ["clean"], _header),
    "an input with more": _set("more", 1, _input),
    "an input of an unknown kind": _set("kind", "other", _input),
    "an input's id not an id": _set("id", "other", _input),
    "inputs too deep": _set("inputs", _nested(MAX_LINEAGE + 1), _header),
}
# These two cases raised a KeyError before headers were checked strictly.
assert {"csv member no_name", "csv member no_columns"} <= set(BROKEN)
# Broken headers of a linkage report, whose only member is JSON.
BROKEN_JSON = {
    **dict(_broken_keys("json member", JSON_MEMBER_KEYS, _json_member)),
    "a member of an unknown type": _set("type", "xml", _json_member),
    "a JSON member with rows": _set("rows", 1, _json_member),
    "a JSON member as CSV": _set("type", "csv", _json_member),
}


class TestBrokenHeaders:
    """Headers that are not as pprl writes them."""

    @pytest.mark.parametrize("change", BROKEN.values(), ids=BROKEN.keys())
    def test_is_damaged(self, tmp_path: Path, change: Callable[[dict], None]) -> None:
        """A header that differs from what pprl writes counts as damaged.

        This covers a missing key, a value of the wrong type, and any other
        difference.
        """
        header, parts = _valid(tmp_path)
        change(header)
        _assert_damaged(_framed(tmp_path / "broken.pprl", header, parts))

    @pytest.mark.parametrize("change", BROKEN_JSON.values(), ids=BROKEN_JSON.keys())
    def test_json_is_damaged(
        self, tmp_path: Path, change: Callable[[dict], None]
    ) -> None:
        """A JSON member that differs from what pprl writes counts as damaged."""
        header, parts = _valid(tmp_path, "linkage_report")
        change(header)
        _assert_damaged(_framed(tmp_path / "broken.pprl", header, parts))

    @pytest.mark.parametrize("kind", ["linkages", "linkage_report", "bundle"])
    def test_valid_is_read(self, tmp_path: Path, kind: str) -> None:
        """These tests frame files the way pprl does: a valid header reads back.

        This way each broken header fails only because of what is broken.
        """
        header, parts = _valid(tmp_path, kind)
        _assert_reads(_framed(tmp_path / "valid.pprl", header, parts))

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("site", None),
            ("site", "x" * _pprl_file.MAX_SITE),
            ("release", "1!3.0.0rc1.dev0+local-1"),
            ("created", "2026-11-16T15:15:02+00:00"),
            ("inputs", []),
            ("inputs", _nested(MAX_LINEAGE)),
        ],
        ids=[
            "no site",
            "longest site",
            "every release character",
            "UTC",
            "no inputs",
            "deepest inputs",
        ],
    )
    def test_allowed(self, tmp_path: Path, key: str, value: object) -> None:
        """Values that a header may hold are not refused."""
        header, parts = _valid(tmp_path)
        header[key] = value
        path = _framed(tmp_path / "allowed.pprl", header, parts)
        for read in (
            lambda: _pprl_file.read(path, check_release=False),
            lambda: _pprl_file.read(path, check_release=False, lazy=True),
            lambda: _pprl_file.read_header(path),
        ):
            read()

    def test_no_rows(self, tmp_path: Path) -> None:
        """A CSV member with no rows is well formed."""
        frame = pd.DataFrame({"study_id": [], "hash": []}, dtype=str)
        path = tmp_path / "empty.pprl"
        _pprl_file.write(path, "hashes", {"hashes": frame}, provenance=provenance())
        _assert_reads(path)

    @pytest.mark.parametrize("key", ["no_role", *VALUES, "coordinator"])
    def test_bundle_role(self, tmp_path: Path, key: str) -> None:
        """A bundle's header has a role that a bundle can have."""
        header, parts = _valid(tmp_path, "bundle")
        if key == "no_role":
            del header["role"]
        else:
            header["role"] = VALUES.get(key, key)
        _assert_damaged(_framed(tmp_path / "broken.pprl", header, parts))

    @pytest.mark.parametrize(
        "data",
        [b"[]", b"1", b"null", b'"text"', b"{", b"\xff", b"[" * 200_000],
        ids=["list", "number", "null", "text", "cut short", "not UTF-8", "too deep"],
    )
    def test_not_a_header(self, tmp_path: Path, data: bytes) -> None:
        """A header that is not a JSON object, or not JSON, is damaged."""
        _assert_damaged(_framed(tmp_path / "broken.pprl", data, []))


class TestHeaderSize:
    """A header's declared size."""

    def _padded(self, folder: Path, size: int) -> Path:
        """Pad a valid header with spaces to ``size`` bytes, as a file."""
        header, parts = _valid(folder)
        data = json.dumps(header).encode("utf-8")
        return _framed(folder / "padded.pprl", data + b" " * (size - len(data)), parts)

    def test_over_the_limit(self, tmp_path: Path) -> None:
        """A 2 MiB header counts as damaged, even though it is otherwise valid."""
        _assert_damaged(self._padded(tmp_path, 2 * MAX_HEADER))

    def test_at_the_limit(self, tmp_path: Path) -> None:
        """A header exactly as long as the limit is read."""
        _assert_reads(self._padded(tmp_path, MAX_HEADER))

    def test_declared_not_read(self, tmp_path: Path) -> None:
        """A file that declares an enormous header length counts as damaged.

        Nothing is read to fill it.
        """
        path = tmp_path / "huge.pprl"
        first = FIRST_LINE.format(kind="hashes", release="3.0.0").encode("ascii")
        path.write_bytes(first + zlib.compress(LENGTH.pack(2**63)))
        _assert_damaged(path)


CSV_OTHER = {"name": "other", "type": "csv", "rows": 0, "columns": ["a"]}
JSON_OTHER = {"name": "other", "type": "json"}
# Kinds of file that need a particular member to be usable, and its type.
NEEDED = {
    "bundle": (Member.SETTINGS, "json"),
    "private_key": (Member.KEY, "json"),
    "public_key": (Member.KEY, "json"),
    "secret": (Member.KEY, "json"),
    "selected": (Member.COHORT, "csv"),
    "clean": (Member.PATIENTS, "csv"),
    "hashes": (Member.HASHES, "csv"),
    "linkages": (Member.LINKAGES, "csv"),
}


def _csv(name: str, columns: Sequence[str] = ("a",)) -> dict:
    return {"name": name, "type": "csv", "rows": 0, "columns": list(columns)}


class TestMembersOfEachKind:
    """The members a file of each kind must have."""

    def _written(self, folder: Path, kind: str, members: list) -> Path:
        """Write a file of ``kind`` whose header describes ``members``.

        Each member is as small as its type allows.
        """
        header, _parts = _valid(folder)
        header |= {"kind": kind, "members": members}
        if kind == "bundle":
            header["role"] = "site"
        parts = [b"a\n" if m["type"] == "csv" else b"{}" for m in members]
        return _framed(folder / "file.pprl", header, parts)

    @pytest.mark.parametrize("kind", NEEDED)
    def test_without_its_member(self, tmp_path: Path, kind: str) -> None:
        """A file missing the member its kind needs counts as damaged."""
        _assert_damaged(self._written(tmp_path, kind, [CSV_OTHER]))

    @pytest.mark.parametrize("kind", NEEDED)
    def test_member_of_the_wrong_type(self, tmp_path: Path, kind: str) -> None:
        """Each required member must have the type pprl writes it as."""
        name, kept = NEEDED[kind]
        member = {"name": name, "type": "json"} if kept == "csv" else _csv(name)
        _assert_damaged(self._written(tmp_path, kind, [member]))

    @pytest.mark.parametrize("kind", ["selected", "tables"])
    def test_tables_are_csv(self, tmp_path: Path, kind: str) -> None:
        """Every table of a selected or a tables file is a CSV member."""
        members = [_csv(Member.COHORT), JSON_OTHER]
        _assert_damaged(self._written(tmp_path, kind, members))

    @pytest.mark.parametrize(
        ("kind", "name", "columns"),
        [
            ("hashes", Member.HASHES, ["study_id", "other"]),
            ("linkages", Member.LINKAGES, ["group_id", "study_id"]),
        ],
    )
    def test_without_its_columns(
        self, tmp_path: Path, kind: str, name: str, columns: list[str]
    ) -> None:
        """A member missing the columns that commands read counts as damaged."""
        _assert_damaged(self._written(tmp_path, kind, [_csv(name, columns)]))

    def test_link_says_damaged(self, tmp_path: Path) -> None:
        """Pprl link refuses a hash file without its hashes as damaged.

        It does not fail with an unexpected error.
        """
        make_workspace(tmp_path, role="linkage")
        for site in SITES:
            header, _parts = _valid(tmp_path)
            header |= {
                "kind": "hashes",
                "site": site,
                "members": [CSV_OTHER],
                **provenance(site),
            }
            _framed(tmp_path / f"{site}.hashes.pprl", header, [b"a\n"])
        rc, output = run_in(tmp_path, ["link"])
        assert rc == 1, output
        assert shows(msg.FILE_DAMAGED, output), output
        assert not shows(msg.UNEXPECTED, output), output

    def test_pack_says_damaged(self, tmp_path: Path) -> None:
        """Pprl pack refuses a cleaned patient file without study_ids as damaged.

        It does not fail with an unexpected error.
        """
        _site_workspace(tmp_path, "site_a")
        for command in ("select", "clean"):
            rc, output = run_in(tmp_path, [command])
            assert rc == 0, output
        path = tmp_path / "site_a.clean.pprl"
        clean = _pprl_file.read(path)
        patients = {Member.PATIENTS: clean.members[Member.PATIENTS]}
        write_again(path, patients, clean.header)
        rc, output = run_in(tmp_path, ["pack"])
        assert rc == 1, output
        assert shows(msg.FILE_DAMAGED, output), output
        assert not shows(msg.UNEXPECTED, output), output


class TestDecompression:
    """Reading a payload, however much it expands."""

    def _payload(self, data: bytes) -> _Payload:
        return _pprl_file._Payload(io.BytesIO(zlib.compress(data)))

    def test_small_file_of_much_data(self, tmp_path: Path) -> None:
        """A small file that expands to far more is read one block at a time.

        So rejecting it takes little memory.
        """
        expanded = 64 * BLOCK
        path = tmp_path / "expands.pprl"
        first = FIRST_LINE.format(kind="hashes", release="3.0.0").encode("ascii")
        body = LENGTH.pack(10) + bytes(expanded)
        path.write_bytes(first + zlib.compress(body))
        tracemalloc.start()
        try:
            with pytest.raises(PprlFileError):
                _pprl_file.read_header(path)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < expanded / 4

    @pytest.mark.parametrize(
        "data",
        [
            bytes(3 * BLOCK),
            os.urandom(3 * BLOCK + 17),
            bytes(BLOCK) + os.urandom(BLOCK) + bytes(BLOCK + 1),
        ],
        ids=["exact blocks of one byte", "random", "both"],
    )
    @pytest.mark.parametrize("step", [1000, BLOCK, 3 * BLOCK + 1])
    def test_reads_all(self, data: bytes, step: int) -> None:
        """A payload reads back as what was compressed, and then ends.

        This holds however much it expands, and for reads of any size.
        """
        payload = self._payload(data)
        read = bytearray()
        while len(read) < len(data):
            read += payload.read(min(step, len(data) - len(read)))
        assert read == data
        assert payload.at_end()

    def test_cut_short(self) -> None:
        """A payload that stops before its stream ends raises EOFError."""
        compressed = zlib.compress(os.urandom(2 * BLOCK))
        payload = _pprl_file._Payload(io.BytesIO(compressed[:-100]))
        with pytest.raises(EOFError):
            payload.read(2 * BLOCK)


def _depth(inputs: list) -> int:
    """Return how deeply a file's ``inputs`` are nested."""
    return max((1 + _depth(entry["inputs"]) for entry in inputs), default=0)


def _site_workspace(folder: Path, site: str) -> None:
    """Make ``site``'s workspace ``folder``, with its patients and tables."""
    make_workspace(folder, site=site, tables=SITE_TABLES)
    write_text(
        folder / "patients.csv",
        "person_id,name,city,state,zip,dob\n"
        + "".join(f"{i},Pat {i},Boston,MA,02101,1990-01-0{i}\n" for i in range(1, 6)),
    )
    write_text(
        folder / "tables" / "scores.csv",
        "person_id,score\n" + "".join(f"{i},{i}\n" for i in range(1, 6)),
    )


@pytest.fixture(scope="module")
def written(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    """Return every pprl file that a project's roles write, made by the commands."""
    root = tmp_path_factory.mktemp("project")
    coordinator_workspace(root / "coordinator")
    agent = root / "linkage"
    make_workspace(agent, role="linkage")
    for site in SITES:
        folder = root / site
        _site_workspace(folder, site)
        for command in ("select", "clean", "hash", "pack"):
            rc, output = run_in(folder, [command])
            assert rc == 0, output
        shutil.copy(folder / "outbox" / "linkage" / f"{site}.hashes.pprl", agent)
    rc, output = run_in(agent, ["link"])
    assert rc == 0, output
    return sorted(root.rglob("*.pprl"))


class TestRoundTrip:
    """A pprl file reads back as it was written."""

    def test_every_kind(self, written: list[Path]) -> None:
        """Every kind of file pprl writes reads back."""
        kinds = set()
        for path in written:
            _assert_reads(path)
            kinds.add(_pprl_file.read_header(path)["kind"])
        assert kinds == set(KINDS)

    def test_lineage_within_the_limit(self, written: list[Path]) -> None:
        """The longest real chain of files is 4 long, ending in a linkage file.

        The linkage file's ``inputs`` are 3 deep, well within the limit.
        """
        depths = [_depth(_pprl_file.read_header(p)["inputs"]) for p in written]
        assert max(depths) == 3 < MAX_LINEAGE
