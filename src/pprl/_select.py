"""Run ``pprl select``: keep the rows of a site's tables that are in the study.

The site's data team exports each table the project merges into the
workspace's ``tables/`` folder, keyed by the site's own id. select is the
site's first command. It keeps only the rows that the project's rules keep,
and writes them to one pprl file, which stays in the workspace. pprl clean
then prepares only the patients those rows belong to, so no other patient
is hashed. pprl pack later packs the rows that select kept.

* If a table is missing, is not a well-formed table, or does not have
  exactly its expected columns, select stops and writes nothing.
* select also stops if two kept rows share a primary key. The message names
  their row numbers.
* Each table's rules are applied in order, and select reports how many rows
  each rule left out. Rows that are left out are not written anywhere.
* select warns about a one_of rule that left out values it does not list
  and kept no rows for some value it does list. This usually means a value
  was renamed at the site, which would otherwise drop rows from the study
  unnoticed. The warning counts the unlisted values (up to
  UNLISTED_COUNTED, then "more than"). It names only the listed values,
  which come from the project's rules, not the site's data.
* A censored column is emptied in every kept row.

A table can be far larger than memory (tens of millions of rows), so select
never holds a whole table in memory. It reads each table in chunks, checks
it, and writes its rows into buckets on disk, inside the workspace. Each
bucket holds the same subset of patients in every table. select then
applies the rules one bucket at a time, starting with the tables that other
rules look in. It writes the kept rows one bucket
at a time.

The file's first member lists, by id, the patients of every kept row
(``Member.COHORT``). Clean reads this member. Then comes each table, in the
bundle's order.
"""

import itertools
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from . import _messages as msg
from . import _pprl_file, _wording
from ._inputs import check_exact_columns, check_id_column, check_output, log_csv
from ._io import FIRST_DATA_ROW, Buckets, csv_chunks, display_name, scratch
from ._messages import Label
from ._rules import bucket_keys, check_site_tables, described, in_order, kept, parts
from ._shared import Member
from ._ui import (
    ValidationContext,
    add_work,
    advance,
    finish_progress,
    note_user,
    ok_user,
    show,
    step,
    stoppable,
    warn_user,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    import numpy as np

    from ._pprl_file import Spooled

# A bucket holds the rows from about this many bytes of the largest table's
# export. That is roughly a million rows, or a few hundred MB once read.
BUCKET_BYTES = 64 * 1024**2
# The steps of select's progress bar, with each step's share of the bar.
# Writing has two steps: first the working file, then the pprl file.
STEPS = (
    (msg.STEP_READING_TABLES, 56),
    (msg.STEP_SELECTING, 31),
    (msg.STEP_WRITING_SELECTED, 7),
    (msg.STEP_WRITING_SELECTED, 6),
)
# The bucket column that holds each row's row number in the export.
_ROW = "__row__"
# The most unlisted values that select counts for a one_of rule. A free-text
# column may have as many values as rows, so past this the warning says
# "more than".
UNLISTED_COUNTED = 1000


def _report(exported: dict[str, _Table]) -> None:
    """Show, for each table, the rows kept and what each rule left out.

    Also show the censored columns, and values that the rules do not list.
    """
    for name, table in exported.items():
        note_user(
            msg.SELECT_TABLE_REPORT(
                table=name, kept=table.kept_buckets.rows, rows=table.rows
            )
        )
        for rule, left_out in zip(table.settings["keep"], table.left_out, strict=True):
            show(msg.SELECT_RULE_REPORT(count=left_out, rule=described(rule)), hang=2)
        if table.settings["censor"]:
            show(
                msg.SELECT_CENSORED(columns=_wording.listed(table.settings["censor"])),
                hang=2,
            )
        for column, count, absent in _unexpected(table):
            warn_user(
                msg.SELECT_VALUES_UNLISTED(
                    table=name,
                    column=column,
                    values=_wording.counted_up_to(count, UNLISTED_COUNTED, "value(s)"),
                    absent=_wording.listed(absent),
                )
            )


def _read_tables(
    ctx: ValidationContext,
    tables: dict,
    exports: str,
    data_folder: Path | str,
    *,
    id_column: str,
    working: Path,
) -> tuple[dict[str, _Table], int]:
    """Read each of the site's tables into buckets.

    Return the _Tables by name, and the number of buckets each table's rows
    go into.
    """
    size = sum(_export_sizes(tables, exports, data_folder))
    stoppable("select", size)
    add_work(size)
    count = _bucket_count(tables, exports, data_folder)
    exported = {}
    for name, settings in tables.items():
        table = _read_table(
            ctx,
            name,
            settings,
            exports,
            data_folder,
            id_column=id_column,
            count=count,
            working=working,
        )
        if table is not None:
            exported[name] = table
    return exported, count


def _selected(
    ctx: ValidationContext,
    tables: dict,
    exported: dict[str, _Table],
    *,
    id_column: str,
    count: int,
) -> set[str]:
    """Apply every table's rules in order and check its keys. Return the cohort."""
    cohort: set[str] = set()
    step(msg.STEP_SELECTING, count * len(tables))
    for name in in_order(tables):
        _select(exported, name, id_column, cohort)
    for name, table in exported.items():
        _check_keys(ctx, name, table)
    return cohort


def select(
    tables: dict | None = None,
    id_column: str | None = None,
    exports: str | None = None,
    output: str | None = None,
    *,
    provenance: dict,
    data_folder: Path | str,
    output_folder: Path | str,
) -> int:
    """Write the rows of a site's tables that the project's rules keep.

    Parameters
    ----------
    tables : dict
        Each table's columns, primary key and rules, from the bundle.
    id_column : str
        The name of the site's id column (the project's `id`).
    exports : str
        The folder of the site's tables, each <table>.csv (in data_folder).
    output : str
        Filename of the pprl file to write the rows kept to (in
        output_folder).
    provenance : dict
        The ``project``, bundle ``set`` and ``site`` the file is for.

    Returns
    -------
    int
        0 on success, 1 on validation failure.
    """
    ctx = ValidationContext()
    id_ok = check_id_column(ctx, id_column)
    tables_ok = check_site_tables(ctx, tables, id_column if id_ok else None)
    out_path = check_output(ctx, Label.OUTPUT_FILE, "output", output, output_folder)
    with scratch(output_folder) as working:
        exported: dict[str, _Table] = {}
        cohort: set[str] = set()
        if tables_ok and id_ok:
            # check_site_tables and check_id_column have found these usable,
            # and exports is a fixed name.
            if not isinstance(tables, dict) or not isinstance(id_column, str):
                raise AssertionError
            if exports is None:
                raise AssertionError
            exported, count = _read_tables(
                ctx, tables, exports, data_folder, id_column=id_column, working=working
            )
            if exported and not ctx.has_failures:
                cohort = _selected(
                    ctx, tables, exported, id_column=id_column, count=count
                )
        if ctx.conclude():
            return 1
        if out_path is None:  # Only after a failure, which conclude has reported
            raise AssertionError

        step(
            msg.STEP_WRITING_SELECTED,
            sum(t.kept_buckets.rows for t in exported.values()),
        )
        members: dict[str, pd.DataFrame | Spooled] = {
            Member.COHORT: pd.DataFrame({id_column: sorted(cohort)}, dtype=str)
        }
        members |= {
            name: _spooled(table, working / f"{name}.csv")
            for name, table in exported.items()
        }
        step(msg.STEP_WRITING_SELECTED)
        _pprl_file.write(
            out_path,
            "selected",
            members,
            provenance=provenance,
        )
    finish_progress()
    _report(exported)
    ok_user(
        msg.WROTE_SELECTED(
            count=len(exported), output=display_name(out_path), people=len(cohort)
        )
    )
    return 0


@dataclass
class _Table:
    """A table that has been read and checked, and then selected.

    It holds:

    - the export's path and the table's settings
    - its rows in buckets on disk, each with its row number in the export
    - the kept rows once selected, in buckets of their own
    - how many rows the export has, and how many each rule left out
    - the rows that repeat a primary key
    - for each one_of rule (keyed by its position in ``keep``), the listed
      values it found and the unlisted values it left out. At most
      UNLISTED_COUNTED + 1 unlisted values are kept.
    """

    path: Path
    settings: dict
    buckets: Buckets
    kept_buckets: Buckets
    rows: int = 0
    left_out: list = field(default_factory=list)
    repeated: _wording.Some = field(default_factory=_wording.Some)
    listed_seen: dict = field(default_factory=dict)
    unlisted: dict = field(default_factory=dict)


def _export_sizes(tables: dict, exports: str, data_folder: Path | str) -> list:
    """Return the size in bytes of each table export that exists."""
    return [
        path.stat().st_size
        for name in tables
        if (path := Path(data_folder) / exports / f"{name}.csv").is_file()
    ]


def _bucket_count(tables: dict, exports: str, data_folder: Path | str) -> int:
    """Count the buckets that every table's rows go into.

    There are enough buckets for the largest export to fit in BUCKET_BYTES
    per bucket.
    """
    sizes = _export_sizes(tables, exports, data_folder)
    return max(1, math.ceil(max(sizes, default=0) / BUCKET_BYTES))


def bucket_of(ids: pd.Series, count: int) -> np.ndarray:
    """Find the bucket, out of ``count`` buckets, for each of ``ids``.

    ``ids`` is a Series of strings. Every row of a patient goes into the same
    bucket, in every table.
    """
    # pandas' type stubs leave pd.util untyped.
    hashes = pd.util.hash_array(ids.to_numpy(dtype=object))  # ty: ignore[call-non-callable]
    return hashes % count


def _read_table(
    ctx: ValidationContext,
    name: str,
    settings: dict,
    exports: str,
    data_folder: Path | str,
    *,
    id_column: str,
    count: int,
    working: Path,
) -> _Table | None:
    """Read a site's table ``name`` one chunk at a time, and return a _Table or None.

    The table must have exactly its expected columns. As rows are read, they
    are written into buckets in ``working``, each with its row number.
    """
    label = Label.TABLE(table=name)
    path = Path(data_folder) / exports / f"{name}.csv"
    if not path.is_file():
        ctx.fail(
            label,
            msg.SELECT_TABLE_MISSING(name=f"{exports}/{name}.csv", folder=exports),
        )
        return None
    columns = settings["columns"]
    table = _Table(
        path,
        settings,
        Buckets(working, name, count, [_ROW, *columns]),
        Buckets(working, f"{name}.kept", count, columns),
    )
    header, columns_ok = None, False
    with ctx.attempt(label, ValueError) as attempt:
        chunks, _ = csv_chunks(path, on_read=advance)
        for chunk in chunks:
            if header is None:
                header = chunk.columns
                columns_ok = check_exact_columns(
                    ctx,
                    Label.TABLE_COLUMNS(table=name),
                    chunk,
                    columns,
                    path,
                )
            table.rows += len(chunk)
            if columns_ok:
                rows = (
                    chunk[columns]
                    .set_axis(chunk.index + FIRST_DATA_ROW)
                    .reset_index(names=_ROW)
                )
                table.buckets.append(rows, bucket_of(rows[id_column], count))
        if header is None:  # Never: csv_chunks yields at least one chunk.
            raise AssertionError
        log_csv(path, table.rows, len(header))
    if attempt.failed or not columns_ok:
        return None
    return table


def _select(
    exported: dict[str, _Table], name: str, id_column: str, cohort: set[str]
) -> None:
    """Apply the rules of the table ``name`` to its rows, one bucket at a time.

    Every table that these rules look in must already be selected. Write the
    kept rows to buckets of their own. Count what each rule left out and the
    rows that repeat a primary key. Add the kept rows' patients to
    ``cohort``.
    """
    table = exported[name]
    settings = table.settings
    table.left_out = [0] * len(settings["keep"])
    kept_buckets = {other: t.kept_buckets for other, t in exported.items()}
    for b, bucket in enumerate(table.buckets):
        rows = bucket
        keys = bucket_keys(kept_buckets, b)
        for n, rule in enumerate(settings["keep"]):
            meets = kept(rule, rows, keys)
            table.left_out[n] += int((~meets).sum())
            if parts(rule)[0] == "one_of":
                ((column, _),) = parts(rule)[1].items()
                table.listed_seen.setdefault(n, set()).update(rows.loc[meets, column])
                unlisted = table.unlisted.setdefault(n, set())
                if len(unlisted) <= UNLISTED_COUNTED:
                    left = rows.loc[~meets, column]
                    unlisted.update(left[left != ""])
                    if len(unlisted) > UNLISTED_COUNTED:  # enough for "more than"
                        table.unlisted[n] = set(
                            itertools.islice(unlisted, UNLISTED_COUNTED + 1)
                        )
            rows = rows[meets]
        for column in settings["censor"]:
            rows[column] = ""
        repeated = rows.duplicated(settings["primary_key"], keep=False)
        table.repeated.extend(sorted(rows.loc[repeated, _ROW].astype(int)))
        cohort.update(rows.loc[rows[id_column] != "", id_column])
        table.kept_buckets.append(rows, b)
        advance()


def _unexpected(table: _Table) -> Iterator[tuple[str, int, list[str]]]:
    """List each one_of rule that left out unlisted values and missed a listed one.

    These are the one_of rules of a selected table that left out values they
    do not list, and kept no rows for some value they do list. For each, give
    its column, how many values it left out (capped just past
    UNLISTED_COUNTED), and the listed values it never found. The listed
    values come from the project's rules, never from the site's data.
    """
    for n, rule in enumerate(table.settings["keep"]):
        if parts(rule)[0] != "one_of" or not table.unlisted.get(n):
            continue
        ((column, values),) = parts(rule)[1].items()
        absent = [v for v in values if v not in table.listed_seen.get(n, set())]
        if absent:
            yield column, len(table.unlisted[n]), absent


def _check_keys(ctx: ValidationContext, name: str, table: _Table) -> None:
    """Check that every row select kept from a table has a unique primary key."""
    label = Label.TABLE_KEY(table=name)
    columns = _wording.listed(table.settings["primary_key"])
    if len(table.repeated):
        ctx.fail(
            label,
            msg.SELECT_KEY_REPEATED(
                count=len(table.repeated),
                name=display_name(table.path),
                columns=columns,
                rows=table.repeated,
            ),
        )


def _spooled(table: _Table, spool: Path) -> Spooled:
    """Write a table's kept rows to ``spool``, as the selected file's member.

    The rows are read from the table's buckets, one bucket at a time.
    """
    rows = table.kept_buckets.spool(spool, on_rows=advance)
    return _pprl_file.Spooled(spool, rows, table.settings["columns"])
