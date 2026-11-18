"""Test handling files too large for memory.

CSV files are read in chunks, pprl files are written and read one member at
a time, and pack works in buckets on disk. Each approach gives the same
result as reading or writing a file all at once, with the same messages in
the same order.
"""

import errno
from typing import TYPE_CHECKING, Never

import pandas as pd
import pytest

from pprl import _messages as msg
from pprl import _pack, _pprl_file, _select
from pprl._io import csv_chunks, for_appending, read_csv_frame, write_text
from pprl._shared import Member
from tests.helpers import (
    SITE_TABLES,
    files_in,
    make_workspace,
    provenance,
    run_in,
    shows,
    write_bundle,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.reliability


def _chunks(path: Path, rows: int) -> list[pd.DataFrame]:
    """Return every chunk of a CSV file, as csv_chunks reads it."""
    chunks, _ = csv_chunks(path, chunk_rows=rows)
    return list(chunks)


def _problem(path: Path, rows: int | None = None) -> str:
    """Return the message raised when reading a CSV file, whole or in chunks."""
    with pytest.raises(ValueError) as raised:  # noqa: PT011 (the caller checks the message)
        _chunks(path, rows) if rows else read_csv_frame(path)
    return str(raised.value)


class TestCsvChunks:
    """Reading a CSV file in chunks."""

    def test_same_as_whole(self, tmp_path: Path) -> None:
        """Read in chunks, a file gives the same rows, in order, as read whole.

        Each row has its position as its index.
        """
        path = tmp_path / "t.csv"
        rows = "".join(f'{i},"v, {i}",\n' for i in range(23))
        write_text(path, "﻿id,value,empty\r\n" + rows + "\n\n")
        whole = read_csv_frame(path)
        chunks = _chunks(path, 5)
        assert [len(c) for c in chunks] == [5, 5, 5, 5, 3]
        pd.testing.assert_frame_equal(pd.concat(chunks), whole)
        assert list(whole.index) == list(range(23))

    def test_header_only(self, tmp_path: Path) -> None:
        """A file with only its header gives one chunk, with no rows."""
        path = tmp_path / "t.csv"
        write_text(path, "id,value\n")
        [chunk] = _chunks(path, 5)
        assert list(chunk.columns) == ["id", "value"]
        assert len(chunk) == 0

    def test_problems_after_the_chunks(self, tmp_path: Path) -> None:
        """A ragged row is reported once every row is read, as when read whole.

        Rows without a value for each column are counted, and the first ones
        are listed.
        """
        path = tmp_path / "t.csv"
        rows = "".join("1,2\n" if i % 2 else "1\n" for i in range(60))
        write_text(path, "a,b\n" + rows)
        message = _problem(path, rows=7)
        assert message == _problem(path)
        assert (
            msg.RAGGED_ROWS(
                name="t.csv", count=30, columns=2, rows=list(range(2, 62, 2))
            )
            == message
        )
        assert "and 10 more" in message

    def test_blank_rows(self, tmp_path: Path) -> None:
        """Blank rows at the end are allowed, but one before another row is reported.

        It is reported before a row without a value for each column.
        """
        path = tmp_path / "t.csv"
        write_text(path, "a,b\n1,2\n\n\n")
        assert len(read_csv_frame(path)) == 1
        write_text(path, "a,b\n1\n\n1,2\n", replace=True)
        assert _problem(path, rows=1) == msg.BLANK_ROWS(name="t.csv", rows=[3])

    def test_open_quote_first(self, tmp_path: Path) -> None:
        """A quote left open is reported before a problem with the header.

        This matches the order pprl has always used.
        """
        path = tmp_path / "t.csv"
        write_text(path, 'a,a\n1,2\n"3,4\n')
        assert "well-formed" in _problem(path, rows=1)
        assert _problem(path, rows=1) == _problem(path)

    def test_windows_text(self, tmp_path: Path) -> None:
        """With conversion on, a file saved as Windows text is read as Windows text."""
        path = tmp_path / "t.csv"
        path.write_bytes("name\nJosé\n".encode("cp1252"))
        chunks, converted = csv_chunks(path, convert=True)
        assert converted
        assert next(chunks)["name"].tolist() == ["José"]
        assert "UTF-8" in _problem(path)


def _written(path: Path, members: dict) -> None:
    _pprl_file.write(path, "tables", members, provenance=provenance())


class TestPprlFileStreaming:
    """Writing and reading a pprl file one member at a time."""

    def test_spooled_member(self, tmp_path: Path) -> None:
        """A member written from a CSV file on disk equals one written from memory.

        Both are written from the same rows.
        """
        df = pd.DataFrame({"study_id": ["2", "1"], "note": ["a, b", ""]})
        spool = tmp_path / "member.csv"
        write_text(spool, df.to_csv(index=False, lineterminator="\n"))
        _written(tmp_path / "a.pprl", {"t": df})
        _written(
            tmp_path / "b.pprl", {"t": _pprl_file.Spooled(spool, 2, list(df.columns))}
        )
        a, b = (_pprl_file.read(tmp_path / n) for n in ("a.pprl", "b.pprl"))
        pd.testing.assert_frame_equal(a.members["t"], b.members["t"])

    def test_lazy_chunks(self, tmp_path: Path) -> None:
        """Read lazily, a file has no members loaded, and chunks give a member's rows.

        The rows come in order.
        """
        tables = {
            "first": pd.DataFrame({"x": [str(i) for i in range(10)]}),
            "second": pd.DataFrame({"y": [str(i) for i in range(25)]}),
            "empty": pd.DataFrame({"z": []}, dtype=str),
        }
        _written(tmp_path / "t.pprl", tables)
        lazy = _pprl_file.read(tmp_path / "t.pprl", lazy=True)
        assert lazy.members == {}
        chunks = list(lazy.chunks("second", 10))
        assert [len(c) for c in chunks] == [10, 10, 5]
        assert pd.concat(chunks)["y"].tolist() == tables["second"]["y"].tolist()
        [empty] = list(lazy.chunks("empty", 10))
        assert list(empty.columns) == ["z"]
        assert len(empty) == 0

    def test_damage_found(self, tmp_path: Path) -> None:
        """A file cut short counts as damaged, whether read whole or lazily."""
        _written(tmp_path / "t.pprl", {"t": pd.DataFrame({"x": ["1"] * 1000})})
        data = (tmp_path / "t.pprl").read_bytes()
        (tmp_path / "t.pprl").write_bytes(data[:-20])
        for lazy in (False, True):
            with pytest.raises(_pprl_file.PprlFileError) as raised:
                _pprl_file.read(tmp_path / "t.pprl", lazy=lazy)
            assert "damaged" in str(raised.value)


def _site_with_tables(
    folder: Path, patients: int = 300, rows_each: int = 4, *, prepared: bool = True
) -> None:
    """Make a site's workspace with a table of ``rows_each`` rows per patient.

    The rows are in their own order, with a few rows for patients not in the
    patient file. If ``prepared``, the table is selected and cleaned too.
    Each row's key is its patient and score.
    """
    make_workspace(folder)
    keyed = SITE_TABLES["scores"] | {"primary_key": ["person_id", "score"]}
    write_bundle(folder, tables={"scores": keyed})
    lines = "".join(
        f"p{i},Pat {i},Boston,MA,02101,1990-01-{1 + i % 28:02d}\n"
        for i in range(patients)
    )
    write_text(folder / "patients.csv", "person_id,name,city,state,zip,dob\n" + lines)
    rows = [f"p{(i * 7) % patients},{i}\n" for i in range(patients * rows_each)] + [
        "unknown,1\n",
        "p-x,2\n",
    ]
    write_text(folder / "tables" / "scores.csv", "person_id,score\n" + "".join(rows))
    if prepared:
        assert run_in(folder, ["select"])[0] == 0
        assert run_in(folder, ["clean"])[0] == 0


class TestSelectInBuckets:
    """Select working in buckets on disk."""

    def test_same_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Selecting in many small buckets gives the same tables as one bucket.

        The found_in rule looks in the other table's bucket for the same
        patients. No working file is left behind.
        """
        make_workspace(tmp_path)
        people = [f"p{i},{'F' if i % 3 else ''}\n" for i in range(200)]
        years = [f"p{i % 250},{2020 + i % 7}\n" for i in range(1000)]
        write_text(
            tmp_path / "tables" / "people.csv", "person_id,sex\n" + "".join(people)
        )
        write_text(
            tmp_path / "tables" / "years.csv", "person_id,year\n" + "".join(years)
        )
        write_bundle(
            tmp_path,
            tables={
                "people": {
                    "columns": ["person_id", "sex"],
                    "primary_key": ["person_id"],
                    "keep": [{"found_in": {"table": "years", "on": ["person_id"]}}],
                    "censor": ["sex"],
                },
                "years": {
                    "columns": ["person_id", "year"],
                    "primary_key": ["person_id", "year"],
                    "keep": [{"one_of": {"year": ["2023", "2024", "2025"]}}],
                    "censor": [],
                },
            },
        )
        selected_file = tmp_path / "site_a.selected.pprl"
        selected = {}
        for buckets, size in (("one", 1024**3), ("many", 200)):
            monkeypatch.setattr(_select, "BUCKET_BYTES", size)
            rc, output = run_in(tmp_path, ["select"])
            assert rc == 0, output
            selected[buckets] = {
                name: df.sort_values(list(df.columns), ignore_index=True)
                for name, df in _pprl_file.read(selected_file).members.items()
            }
            selected_file.unlink()
            assert not [n for n in files_in(tmp_path) if ".tmp." in n]
        for name, df in selected["one"].items():
            pd.testing.assert_frame_equal(df, selected["many"][name])
        years = selected["many"]["years"]
        assert set(years["year"]) == {"2023", "2024", "2025"}
        people = selected["many"]["people"]
        assert set(people["person_id"]) == set(years["person_id"]) & {
            f"p{i}" for i in range(200)
        }, "Only the people with a row of the study's years"
        assert set(people["sex"]) == {""}, "Censored"
        assert set(selected["many"][Member.COHORT]["person_id"]) == set(
            years["person_id"]
        )

    @pytest.mark.parametrize(("values", "says"), [(5, "5"), (6, "more than 5")])
    def test_unlisted_values_capped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: int, says: str
    ) -> None:
        """A one_of rule's left-out values are counted up to UNLISTED_COUNTED.

        The count spans buckets. Past the limit, the message says "more than".
        """
        make_workspace(tmp_path)
        keep = [{"one_of": {"score": ["5", "7"]}}]
        write_bundle(
            tmp_path, tables={"scores": SITE_TABLES["scores"] | {"keep": keep}}
        )
        rows = "".join(f"p{i},{100 + i % values}\n" for i in range(200))
        write_text(tmp_path / "tables" / "scores.csv", "person_id,score\n" + rows)
        monkeypatch.setattr(_select, "BUCKET_BYTES", 200)
        monkeypatch.setattr(_select, "UNLISTED_COUNTED", 5)
        rc, output = run_in(tmp_path, ["select"])
        assert rc == 0, output
        assert shows(msg.SELECT_VALUES_UNLISTED, output), output
        assert shows(f"score had {says} values that its rule does not list", output)

    def test_problem_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A table problem found after the first chunks still stops select.

        Nothing is written, and no working file is left behind.
        """
        _site_with_tables(tmp_path, prepared=False)
        with for_appending(tmp_path / "tables" / "scores.csv") as fh:
            fh.write("p1\n")  # A row without its score, at the end
        monkeypatch.setattr(_select, "BUCKET_BYTES", 200)
        rc, output = run_in(tmp_path, ["select"])
        assert rc == 1, output
        assert shows(msg.RAGGED_ROWS, output), output
        assert not (tmp_path / "site_a.selected.pprl").exists()
        assert not [n for n in files_in(tmp_path) if ".tmp." in n]


class TestPackInBuckets:
    """Pack working in buckets on disk."""

    def test_same_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Packing in many small buckets gives the same table as one bucket.

        Rows are in study_id order, and rows with the same study_id stay in
        the order select wrote them. No working file is left behind.
        """
        _site_with_tables(tmp_path)
        packed_file = tmp_path / "outbox" / "merge" / "site_a.tables.pprl"
        packed = {}
        for buckets, size in (("one", 10**9), ("many", 20)):
            monkeypatch.setattr(_pack, "BUCKET_ROWS", size)
            rc, output = run_in(tmp_path, ["pack"])
            assert rc == 0, output
            assert shows(msg.PACK_LEFT_OUT, output), output
            packed[buckets] = _pprl_file.read(packed_file).members["scores"]
            packed_file.unlink()  # Ready for the next run, from the same clean
            assert not [n for n in files_in(tmp_path) if ".tmp." in n]
        pd.testing.assert_frame_equal(packed["one"], packed["many"])
        study_ids = packed["many"]["study_id"].astype(int)
        assert study_ids.is_monotonic_increasing
        assert len(packed["many"]) == 1200

    def test_problem_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A row that breaks a rule, found in the last bucket, still stops pack.

        Nothing is written, and no working file is left behind.
        """
        _site_with_tables(tmp_path)
        study_ids = _pprl_file.read(tmp_path / "site_a.clean.pprl").members[
            Member.STUDY_IDS
        ]
        last = study_ids.loc[study_ids["study_id"].astype(int).idxmax(), "person_id"]
        scores = read_csv_frame(tmp_path / "tables" / "scores.csv")
        broken = scores.loc[scores["person_id"] == last, "score"].iloc[-1]
        allowed = sorted(set(scores["score"]) - {broken})
        keyed = SITE_TABLES["scores"] | {
            "primary_key": ["person_id", "score"],
            "keep": [{"one_of": {"score": allowed}}],
        }
        # The rules change after select ran, so pack is the first to apply them.
        write_bundle(tmp_path, tables={"scores": keyed})
        monkeypatch.setattr(_pack, "BUCKET_ROWS", 20)
        rc, output = run_in(tmp_path, ["pack"])
        assert rc == 1, output
        assert shows(msg.PACK_NOT_SELECTED, output), output
        assert not (tmp_path / "outbox" / "merge" / "site_a.tables.pprl").exists()
        assert not [n for n in files_in(tmp_path) if ".tmp." in n]


class TestDiskFull:
    """A drive that fills up."""

    def test_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A full drive is reported as such, with a fix the user can follow.

        Nothing is written.
        """
        _site_with_tables(tmp_path)

        def full(*_args: object, **_kwargs: object) -> Never:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr("pprl._pprl_file.zlib.compressobj", full)
        rc, output = run_in(tmp_path, ["pack"])
        assert rc == 1, output
        assert shows(msg.DISK_FULL, output), output
        assert not (tmp_path / "outbox" / "merge" / "site_a.tables.pprl").exists()
