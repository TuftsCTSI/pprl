"""Run ``pprl pack``: prepare a site's selected tables for the merge agent.

pack reads the rows that pprl select kept from each table the project
merges. It writes them all into one pprl file. Each row's site id is
replaced with the study_id that pprl clean gave the patient, so no site id
leaves the site.

- If a selected file or cleaned patient file is missing, damaged, or from
  another run, pack stops and writes nothing. (Clean must have read the
  same selected file that pack reads.)
- pack also stops if a row breaks one of the project's rules, which means
  select should not have written it. pack checks
  every rule again.
- A row whose id has no study_id is left out, with a warning that gives the
  count. This happens for a patient that clean set aside, or one that is
  not in the patient file at all.
- Rows are written in study_id order, so their order reveals nothing about
  the order of the site's export.

A table can be far larger than memory (tens of millions of rows), so pack
never holds a whole table in memory. It reads each table in chunks and
writes the rows into buckets on disk, inside the workspace. Each bucket
covers the same range of study_ids in every table. pack then checks the
rules one bucket at a time. Once every table has passed, it sorts each
bucket in turn and writes the buckets in order (see ``_bucketed`` and
``_sorted``).

The packed file names the cleaned patient file it took the study_ids from
as its input. This lets merge confirm that the study_ids match the ones in
the site's hashes.
"""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from . import _messages as msg
from . import _pprl_file
from ._inputs import (
    check_exact_columns,
    check_id_column,
    check_output,
    check_pprl_input,
)
from ._io import CHUNK_ROWS, Buckets, display_name, scratch
from ._messages import Label
from ._pprl_file import PprlFileError, lineage_ids
from ._rules import bucket_keys, check_site_tables, violations
from ._shared import STUDY_ID, Member
from ._ui import (
    ValidationContext,
    advance,
    finish_progress,
    ok_user,
    step,
    stoppable,
    warn_user,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ._pprl_file import PprlFile, Spooled

# A bucket holds about this many rows of the largest table. That is a few
# hundred MB once read, whatever the table's size.
BUCKET_ROWS = 1_000_000
# The steps of pack's progress bar, with each step's share of the bar.
STEPS = (
    (msg.STEP_READING_SELECTED, 2),
    (msg.STEP_REPLACING_IDS, 43),
    (msg.STEP_CHECKING_TABLES, 16),
    (msg.STEP_SORTING, 23),
    (msg.STEP_WRITING_PACKED, 16),
)


def _study_ids(
    ctx: ValidationContext,
    clean_file: PprlFile | None,
    cleaned: str | None,
    id_column: str | None,
) -> pd.DataFrame | None:
    """Return the cleaned patient file's study_ids member.

    Check its columns against ``id_column``, unless that is None. Return None
    if the member is missing; the failure is recorded.
    """
    if clean_file is None:
        return None
    mapping = clean_file.members.get(Member.STUDY_IDS)
    if mapping is None:
        ctx.fail(
            Label.PATIENTS_FILE, msg.FILE_DAMAGED(name=display_name(clean_file.path))
        )
    elif id_column is not None:
        check_exact_columns(
            ctx, Label.STUDY_IDS_COLUMNS, mapping, [id_column, STUDY_ID], cleaned
        )
    return mapping


def pack(
    tables: dict | None = None,
    id_column: str | None = None,
    selected: str | None = None,
    cleaned: str | None = None,
    patients: str | None = None,
    *,
    flagged: str | None = None,
    output: str | None = None,
    provenance: dict,
    data_folder: Path | str,
    output_folder: Path | str,
) -> int:
    """Write each of a site's selected tables with study_ids in place of its ids.

    Parameters
    ----------
    tables : dict
        Each table's columns, primary key and rules, from the bundle.
    id_column : str
        The name of the site's id column (the project's `id`).
    selected : str
        Filename of the selected tables file pprl select wrote.
    cleaned : str
        Filename of the cleaned patient file pprl clean wrote. Its
        ``study_ids`` member gives the study_id for each site id.
    patients, flagged : str
        The names of the patient file that clean read, and of the file of
        rows clean set aside. They are used in the warning about rows left
        out.
    output : str
        Filename of the pprl file to write the tables to (in output_folder).
    provenance : dict
        The ``project``, bundle ``set`` and ``site`` the files are for.

    Returns
    -------
    int
        0 on success (even with rows left out), 1 on validation failure.
    """
    ctx = ValidationContext()
    site = provenance["site"]
    id_ok = check_id_column(ctx, id_column)
    tables_ok = check_site_tables(ctx, tables, id_column if id_ok else None)
    out_path = check_output(ctx, Label.OUTPUT_FILE, "output", output, output_folder)
    selected_file = check_pprl_input(
        ctx,
        Label.SELECTED_FILE,
        selected,
        data_folder,
        "selected",
        provenance=provenance,
        site=site,
        lazy=True,
    )
    clean_file = check_pprl_input(
        ctx,
        Label.PATIENTS_FILE,
        cleaned,
        data_folder,
        "clean",
        provenance=provenance,
        site=site,
    )
    if (
        selected_file
        and clean_file
        and lineage_ids(clean_file.header["inputs"], "selected")
        != {site: selected_file.id}
    ):
        ctx.fail(
            Label.SELECTED_FILE,
            msg.PACK_OTHER_SELECTION(cleaned=cleaned, selected=selected),
        )
        selected_file = None
    mapping = _study_ids(ctx, clean_file, cleaned, id_column if id_ok else None)
    with scratch(output_folder) as working:
        packed = {}
        if tables_ok and selected_file and not ctx.has_failures:
            # These are None only after a failure.
            if tables is None or mapping is None or id_column is None:
                raise AssertionError
            described = selected_file.header["members"]
            size = sum(m["rows"] for m in described if m["name"] in tables)
            stoppable("pack", size)
            step(msg.STEP_REPLACING_IDS, size)
            study_id_of = pd.Series(
                mapping[STUDY_ID].astype(int).to_numpy(), index=mapping[id_column]
            )
            packed = _bucketed(
                ctx, tables, selected_file, study_id_of, id_column, working=working
            )
            step(msg.STEP_CHECKING_TABLES, sum(len(t.buckets) for t in packed.values()))
            for name in packed:
                _check_rules(ctx, name, packed, selected_file)
        if ctx.conclude():
            return 1
        # These are None only after a failure, which conclude has reported.
        if id_column is None or out_path is None or clean_file is None:
            raise AssertionError

        step(msg.STEP_SORTING, sum(t.buckets.rows for t in packed.values()))
        for name, table in packed.items():
            if table.unknown:
                warn_user(
                    msg.PACK_LEFT_OUT(
                        count=table.unknown,
                        table=name,
                        patients=patients,
                        flagged=flagged,
                    )
                )
        spooled = {
            name: _sorted(table, working / f"{name}.csv", id_column)
            for name, table in packed.items()
        }
        step(msg.STEP_WRITING_PACKED)
        _pprl_file.write(
            out_path,
            "tables",
            spooled,
            provenance=provenance,
            inputs=[clean_file],
        )
    finish_progress()
    ok_user(
        msg.WROTE_TABLES(
            count=len(spooled), output=display_name(out_path), id=id_column
        )
    )
    return 0


@dataclass
class _Table:
    """A selected table's settings, and its rows in buckets on disk.

    Each row has its study_id in place of its id (see ``_bucketed``). The
    object also counts the rows left out because their ids have no study_id.
    """

    settings: dict
    buckets: Buckets
    unknown: int = 0


def _bucketed(
    ctx: ValidationContext,
    tables: dict,
    selected_file: PprlFile,
    study_id_of: pd.Series,
    id_column: str,
    *,
    working: Path,
) -> dict[str, _Table]:
    """Write each table of the selected file into buckets in ``working``.

    Read one chunk at a time, and keep only rows that have a study_id. Rows
    go into buckets by study_id range, using the same ranges for every table.
    Return the _Tables by name. Return None if the file does not hold the
    tables in ``tables`` the way select writes them.
    """
    found = {name: selected_file.member(name) for name in tables}
    described = {name: member for name, member in found.items() if member is not None}
    if len(described) != len(found) or any(
        described[name].get("columns") != settings["columns"]
        for name, settings in tables.items()
    ):
        ctx.fail(
            Label.SELECTED_FILE,
            msg.FILE_DAMAGED(name=display_name(selected_file.path)),
        )
        return {}
    largest = max(described[name]["rows"] for name in tables)
    count = max(1, math.ceil(largest / BUCKET_ROWS))
    total = max(len(study_id_of), 1)
    packed = {}
    with ctx.attempt(Label.SELECTED_FILE, PprlFileError):
        for name, settings in tables.items():
            table = _Table(settings, Buckets(working, name, count, settings["columns"]))
            for chunk in selected_file.chunks(name, CHUNK_ROWS):
                study_ids = chunk[id_column].map(study_id_of)
                known = study_ids.notna()
                table.unknown += int((~known).sum())
                rows = chunk[known].copy()
                study_ids = study_ids[known].astype(int)
                rows[id_column] = study_ids.astype(str)
                table.buckets.append(rows, (study_ids - 1) * count // total)
                advance(len(chunk))
            packed[name] = table
        return packed
    return {}


def _check_rules(
    ctx: ValidationContext,
    name: str,
    packed: dict[str, _Table],
    selected_file: PprlFile,
) -> None:
    """Check that every row of the table ``name`` meets the project's rules.

    Select should have written only such rows. Check one bucket at a time,
    and return whether every row passes.
    """
    table = packed[name]
    buckets = {other: t.buckets for other, t in packed.items()}
    bad = 0
    for b, rows in enumerate(table.buckets):
        bad += violations(table.settings, rows, bucket_keys(buckets, b))
        advance()
    label = Label.TABLE(table=name)
    if bad:
        ctx.fail(
            label,
            msg.PACK_NOT_SELECTED(
                count=bad, table=name, name=display_name(selected_file.path)
            ),
        )


def _sorted(table: _Table, spool: Path, id_column: str) -> Spooled:
    """Write a table's rows to ``spool`` in study_id order, as the packed member.

    Work from the table's buckets, one bucket at a time. Rows with the same
    study_id keep the order select wrote them in. The study_id replaces the
    id column.
    """
    columns = [STUDY_ID if c == id_column else c for c in table.settings["columns"]]

    def in_order(part: pd.DataFrame) -> pd.DataFrame:
        advance(len(part))
        order = part[id_column].astype(int).sort_values(kind="stable").index
        return part.loc[order].set_axis(columns, axis="columns")

    rows = table.buckets.spool(spool, columns, in_order)
    return _pprl_file.Spooled(spool, rows, columns)
