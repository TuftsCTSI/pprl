"""Run ``pprl merge``: combine linked records from several sites, one row per person.

``merge`` checks the config, the linkage file and every site's tables,
then merges each table. When several sites' records belong to the same
person, strategies resolve the conflicts. A column's ``rank_by`` lists
strategies in order, each comparing a column it names. A tie under one
strategy goes to the next. Tethered columns take their values from the same
row as their lead column. Partitioned tables apply different rules for each
partition value.

Each strategy is a class, listed in ``STRATEGIES``. The class defines the
settings it takes, how it ranks rows, and which values it cannot compare,
along with its wording for them.
"""

import math
import secrets
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING, override

import pandas as pd

from . import _messages as msg
from . import _pprl_file, _wording
from ._dates import are_iso_8601, as_iso_8601
from ._inputs import (
    check_columns,
    check_id_column,
    check_output,
    check_pprl_input,
    check_sites,
)
from ._io import CHUNK_ROWS, PLAIN_NAME, Buckets, for_writing_folder, scratch
from ._messages import Label
from ._pprl_file import file_name, lineage_ids
from ._shared import LINKAGE_COLUMNS, SEPARATOR, STUDY_ID, Member
from ._ui import (
    ValidationContext,
    advance,
    fail,
    finish_progress,
    info_log,
    note_user,
    ok_user,
    show,
    step,
    stoppable,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from ._pprl_file import PprlFile
    from ._wording import Message

# The linkage file's columns: each record's group, and the record itself.
GROUP, SITE, _ = LINKAGE_COLUMNS
# Columns that merge adds to every site's rows while it works: the site each
# row came from and, once rows are in buckets, the person each row belongs to.
SITE_COLUMN = "__site__"
GROUP_COLUMN = "__group_id__"

_TABLE_KEYS = frozenset({"primary_key", "column", "partition_by", "partitions"})
_COLUMN_KEYS = frozenset({"rank_by", "tether", "drop"})


# ---------------------------------------------------------------------------
# The merge command
# ---------------------------------------------------------------------------


# The steps of merge's progress bar, with each step's share of the bar.
STEPS = (
    (msg.STEP_READING_SITES, 2),
    (msg.STEP_SCANNING, 32),
    (msg.STEP_MERGING, 66),
)


def merge(
    linkages: str | None = None,
    sites: list[str] | None = None,
    id_column: str | None = None,
    tables: dict | None = None,
    output: str | None = None,
    *,
    provenance: dict,
    data_folder: Path | str,
    output_folder: Path | str,
) -> int:
    """Merge linked records from multiple sites into one dataset.

    Read a linkage file and each site's packed tables. Resolve conflicts with
    the declared strategies, and write one output CSV file per table.

    Parameters
    ----------
    linkages : str
        Filename of the linkage file (in data_folder).
    sites : list of str
        The sites, in tie-breaking order. Each site's tables are in the pprl
        file site_X.tables.pprl (in data_folder).
    id_column : str
        The project's `id`. The merged tables use this name for each
        person's new id, which replaces the study_id from the sites' tables.
    tables : dict
        Per-table merge configuration (primary keys, strategies, tethers).
    output : str
        Subdirectory name for output files (within output_folder).
    provenance : dict
        The ``project`` and bundle ``set`` that every file must belong to.

    Returns
    -------
    int
        0 on success, 1 on validation failure.
    """
    ctx = ValidationContext()
    check_sites(ctx, sites, msg.MERGE_SITES_NOT_LIST)
    id_ok = check_id_column(ctx, id_column)
    check_tables(ctx, tables, id_column if id_ok else None)
    out_dir = check_output(ctx, Label.OUTPUT, "output", output, output_folder)
    linkage_file = check_pprl_input(
        ctx,
        Label.LINKAGES_FILE,
        linkages,
        data_folder,
        "linkages",
        provenance=provenance,
    )
    site_files = _site_files(ctx, sites, linkage_file, data_folder, provenance)
    if ctx.conclude():
        return 1
    # These are None only after a failure, which conclude has reported.
    if id_column is None or tables is None or sites is None or linkage_file is None:
        raise AssertionError
    if output is None or out_dir is None:
        raise AssertionError
    site_files = _present(site_files)
    linkage_df = linkage_file.members[Member.LINKAGES]
    linked = _linked_people(linkage_df)
    stoppable("merge", _rows_to_merge(site_files, tables))

    with scratch(output_folder) as working:
        # Find every person. Each linked group is one person, and each record
        # not in the linkage file is another.
        sources = _Sources(site_files, id_column, linked)
        people = set(linked.unique())
        scanned = _scanned_tables(tables, sources, people)
        if scanned is None:
            return 1

        # Give each person one new id, the same in every table.
        sources.person_ids = _merged_person_ids(people)
        n_unlinked = len(sources.person_ids) - linkage_df[GROUP].nunique()
        if n_unlinked:
            note_user(msg.MERGE_UNLINKED(count=n_unlinked))

        note_user(msg.MERGE_SITE_ORDER(sites=_wording.listed(sites)))
        try:
            reports, written = _merged_tables(
                tables, scanned, sources, sites, working, out_dir=out_dir
            )
        except _InconsistentError as problem:
            return fail(
                msg.MERGE_INCONSISTENT(table=problem.table, check=problem.check)
            )
    finish_progress()
    _report_merged(reports, written, output)
    return 0


def _site_files(
    ctx: ValidationContext,
    sites: object,
    linkage_file: PprlFile | None,
    data_folder: Path | str,
    provenance: dict,
) -> dict[str, PprlFile | None]:
    """Read and check each site's packed tables, keyed by site.

    Each file is checked (see _check_site_file) against the run of clean
    that produced the site's hashes in the linkage file. Return the
    PprlFile, or None, for each site.
    """
    if not isinstance(sites, list):
        return {}
    runs = lineage_ids(linkage_file.header["inputs"], "clean") if linkage_file else {}
    return {
        site: _check_site_file(ctx, site, data_folder, provenance, runs.get(site))
        for site in sites
    }


def _present(site_files: dict[str, PprlFile | None]) -> dict[str, PprlFile]:
    """Return ``site_files``, all of them read, once the checks have concluded."""
    present = {site: file for site, file in site_files.items() if file is not None}
    if len(present) != len(site_files):  # Never: a failure would have stopped the run.
        raise AssertionError
    return present


def _rows_to_merge(site_files: dict[str, PprlFile], tables: dict) -> int:
    """Count the table rows that merge reads, across every site."""
    return sum(
        member["rows"]
        for site_file in site_files.values()
        for member in site_file.header["members"]
        if member["name"] in tables
    )


def _scanned_tables(
    tables: dict, sources: _Sources, people: set[str]
) -> dict[str, _Scanned] | None:
    """Read and check every table, one chunk at a time, before merging any.

    Add each row's person to ``people``. Return each table's _Scanned, keyed
    by name, or None if any table failed.
    """
    ctx = ValidationContext()
    step(msg.STEP_SCANNING, _rows_to_merge(sources.site_files, tables))
    scanned = {}
    for name, config in tables.items():
        found = _scan(ctx, name, config, sources, people)
        if found is not None:
            scanned[name] = found
    if ctx.conclude():
        return None
    return scanned


def _merged_tables(
    tables: dict,
    scanned: dict[str, _Scanned],
    sources: _Sources,
    sites: list[str],
    working: Path,
    *,
    out_dir: Path,
) -> tuple[dict, dict]:
    """Merge every table into its CSV file in the folder ``out_dir``.

    Merge one bucket of people at a time (see _merge_in_buckets). Either
    every table is written, or, if one fails its check (_InconsistentError),
    none is. Return what merging did and the rows written, each keyed by
    table name.
    """
    reports, written = {}, {}
    # Each row counts twice: once as it goes into a bucket, and once as its
    # bucket is merged.
    step(msg.STEP_MERGING, 2 * sum(table.facts.rows for table in scanned.values()))
    with for_writing_folder(out_dir) as staged:
        staged.mkdir()
        for name, config in tables.items():
            reports[name], written[name] = _merge_in_buckets(
                name,
                config,
                scanned[name],
                sources,
                sites,
                working=working,
                output=staged / f"{name}.csv",
            )
    return reports, written


def _report_merged(reports: dict, written: dict, output: str) -> None:
    """Report what merging each table did, and the folder ``output`` it went to.

    See _show_report.
    """
    for name, report in reports.items():
        _show_report(name, report)
    ok_user(msg.WROTE_MERGED(count=len(written), output=output))


# A bucket holds about this many rows of a table, from all sites together.
# merge holds one bucket at a time, whatever the table's size.
BUCKET_ROWS = 1_000_000


class _InconsistentError(Exception):
    """Raised when a merged table fails merge's check of its own counts."""

    def __init__(self, table: str, check: str) -> None:
        super().__init__(table, check)
        self.table, self.check = table, check


@dataclass
class _Scanned:
    """A table that has been read and checked.

    It holds the table's facts, the columns of every site's rows together,
    and which primary-key columns hold only numbers. Rows are sorted by
    those columns numerically (see _sorted_by_key).
    """

    facts: TableFacts
    columns: list
    numeric: dict


@dataclass
class _Sources:
    """Where merge reads every site's rows, and which person each row belongs to.

    It maps each linked record to its person (see _linked_people). Once
    every table is scanned, it also holds each person's new id (see
    _merged_person_ids).
    """

    site_files: dict
    id_column: str
    linked: pd.Series
    person_ids: dict = field(default_factory=dict)

    def people(self, rows: pd.DataFrame) -> pd.Series:
        """Name the person each of a table's ``rows`` belongs to (see _people)."""
        return _people(rows, self.linked, self.id_column)

    def chunks(self, table_name: str) -> Iterator[pd.DataFrame]:
        """Read every site's rows of a table, one chunk at a time, site by site.

        Each chunk gets SITE_COLUMN, and its study_id becomes the id column.
        """
        for site, site_file in self.site_files.items():
            for chunk in site_file.chunks(table_name, CHUNK_ROWS):
                yield chunk.rename(columns={STUDY_ID: self.id_column}).assign(
                    **{SITE_COLUMN: site}
                )


def _site_columns(
    ctx: ValidationContext, table_name: str, sources: _Sources
) -> list[str] | None:
    """Return a table's columns, combined from every site's copy.

    The study_id column becomes the id column. Return None if a site's table
    is missing or not as pack wrote it.
    """
    id_column = sources.id_column
    columns, ok = [], True
    for site, site_file in sources.site_files.items():
        names = {"table": table_name, "site": site}
        described = site_file.member(table_name)
        if described is None:
            ctx.fail(
                Label.TABLE_SITE(**names),
                msg.SITE_TABLE_MISSING(name=file_name("tables", site), **names),
            )
            ok = False
            continue
        header = pd.DataFrame(columns=described["columns"])
        if not check_columns(
            ctx,
            Label.TABLE_SITE_COLUMNS(**names),
            header,
            (STUDY_ID,),
            table_name,
        ):
            ok = False
            continue
        for name in header.columns:
            column = id_column if name == STUDY_ID else name
            if column not in columns:
                columns.append(column)
    return columns if ok else None


def _scan(
    ctx: ValidationContext,
    table_name: str,
    config: dict,
    sources: _Sources,
    people: set[str],
) -> _Scanned | None:
    """Read and check every site's rows of a table, one chunk at a time.

    The rows are checked against the table's rules (see check_table), and
    each row's person is added to ``people``. Return a _Scanned, or None if
    a site's table is missing or not as pack wrote it.
    """
    id_column = sources.id_column
    columns = _site_columns(ctx, table_name, sources)
    if columns is None:
        return None
    columns.append(SITE_COLUMN)
    rules = config if isinstance(config, dict) else {}
    partition_by = rules.get("partition_by")
    if not isinstance(partition_by, str) or partition_by not in columns:
        partition_by = None
    facts = TableFacts(columns, config, partition_by)
    key = rules.get("primary_key")
    if not isinstance(key, list):
        key = []
    numeric = {c: True for c in key if c != id_column and c in columns}
    label = Label.TABLE(table=table_name)
    with ctx.attempt(label, _pprl_file.PprlFileError) as attempt:
        for chunk in sources.chunks(table_name):
            advance(len(chunk))
            facts.add(chunk)
            people.update(sources.people(chunk).unique())
            for column, so_far in numeric.items():
                numbers = pd.to_numeric(chunk[column], errors="coerce").notna()
                numeric[column] = so_far and bool(numbers.all())
    if attempt.failed:
        return None
    check_table(ctx, table_name, config, facts, id_column)
    return _Scanned(facts, columns, numeric)


def _merge_in_buckets(
    table_name: str,
    config: dict,
    scanned: _Scanned,
    sources: _Sources,
    sites: list[str],
    *,
    working: Path,
    output: Path,
) -> tuple[dict[str | None, PartCounts], int]:
    """Merge a table into the CSV file ``output``, one bucket of people at a time.

    Return what merging did (see merge_table) and the number of rows written.

    Every site's rows are written into buckets in ``working``. Each bucket
    covers a range of new person ids, so all of a person's rows land in one
    bucket. Each bucket is merged the same way the whole table would be,
    checked (see inconsistency), sorted by primary key, and appended after
    the previous bucket. Since the person id is the first column of the
    primary key, the rows come out in the same order as for the whole table.
    A table whose primary key starts with another column is merged as a
    single bucket.
    """
    id_column, primary_key = sources.id_column, config["primary_key"]
    count = 1
    if primary_key[0] == id_column:
        count = max(1, math.ceil(scanned.facts.rows / BUCKET_ROWS))
    stored = [c for c in scanned.columns if c != id_column] + [GROUP_COLUMN]
    buckets = Buckets(working, table_name, count, stored)
    people = len(sources.person_ids)
    for chunk in sources.chunks(table_name):
        ids = sources.people(chunk).map(sources.person_ids)
        rows = chunk.reindex(columns=stored[:-1]).assign(
            **{GROUP_COLUMN: ids.to_numpy()}
        )
        buckets.append(rows, (ids.astype(int) - 1) * count // people)
        advance(len(chunk))

    report = {}
    numeric = {id_column: True, **scanned.numeric}

    def merged(df: pd.DataFrame) -> pd.DataFrame:
        advance(len(df))
        result, found = merge_table(df, config, sites, id_column)
        check = inconsistency(result, primary_key, found)
        if check:
            raise _InconsistentError(table_name, check)
        for part, counts in found.items():
            report.setdefault(part, PartCounts()).add(counts)
        return _sorted_by_key(result, primary_key, numeric)

    rules = [config.get("column", {})]
    if config.get("partition_by"):
        rules = [
            spec.get("column", {})
            for value, spec in config["partitions"].items()
            if value in scanned.facts.values  # noqa: PD011 (a set, not pandas)
        ]
    columns = merged_columns(primary_key, rules)
    return report, buckets.spool(output, columns, merged)


def merged_columns(primary_key: list[str], rules: list[dict]) -> list[str]:
    """List a merged table's columns: its primary key, then the rules' columns.

    The rule columns are those each rule keeps, in the rules' order.
    ``rules`` holds the column rules for each part of the table that has
    rows, in order. An unpartitioned table has one entry. With no entries,
    the table has no columns.
    """
    columns = list(primary_key) if rules else []
    for column_config in rules:
        columns += [
            name
            for name, spec in column_config.items()
            if not spec.get("drop", False) and name not in columns
        ]
    return columns


def inconsistency(
    result: pd.DataFrame, primary_key: list[str], report: dict[str | None, PartCounts]
) -> str | None:
    """Describe what is wrong with a merged table, using merge's own counts.

    Return None if nothing is wrong. The counts are in PartCounts. Each check
    must hold whatever the data, so a failure means a fault in pprl. The
    checks are:

    - Each primary key appears once.
    - No more rows come out than went in.
    - For each column, the rows that differed, and the rows with no value,
      are each no more than the number of rows.
    - The values not kept are no more than the rows merged into others,
      since each merged row keeps one of its values if it has one. For a
      tethered column, the limit also includes the rows with no value,
      because none of their values are kept.
    - Every row that differed has a recorded decision.
    """
    if len(result.columns) and result.duplicated(primary_key).any():
        return msg.MERGE_CHECKS["key"]()
    for part in report.values():
        if part.rows_out > part.rows_in:
            return msg.MERGE_CHECKS["rows"]()
        for column, counts in part.columns.items():
            merged_away = part.rows_in - part.rows_out
            if counts.lead is not None:
                merged_away += counts.empty
            for check, broken in (
                ("conflicts", counts.conflicts > part.rows_out),
                ("changed", counts.changed > merged_away),
                ("empty", counts.empty > part.rows_out),
                (
                    "decided",
                    counts.lead is None
                    and sum(counts.decided.values()) != counts.conflicts,
                ),
            ):
                if broken:
                    return msg.MERGE_CHECKS[check](column=column)
    return None


def _how(counts: ColumnCounts) -> str:
    """Describe, in brackets, what decided a column's rows where the sites differed."""
    if not counts.conflicts:
        return ""
    if counts.lead is not None:
        return f" ({msg.MERGE_TETHERED(lead=counts.lead)})"
    decided = sorted(counts.decided.items(), key=lambda item: -item[1])
    return (
        " ("
        + _wording.listed(
            msg.MERGE_DECIDED(count=count, criterion=criterion)
            for criterion, count in decided
        )
        + ")"
    )


def _show_report(table_name: str, report: dict[str | None, PartCounts]) -> None:
    """Show what merging a table did, column by column, across its partitions.

    Also log the same, partition by partition.
    """
    total = PartCounts()
    for part_value, part in report.items():
        total.add(part)
        part_name = "" if part_value is None else msg.INFO_PARTITION(value=part_value)
        info_log(
            msg.INFO_MERGE_PART(
                table=table_name,
                part=part_name,
                rows_in=part.rows_in,
                rows_out=part.rows_out,
            )
        )
        for column, counts in part.columns.items():
            info_log(
                msg.INFO_MERGE_COLUMN(
                    table=table_name,
                    part=part_name,
                    column=column,
                    conflicts=counts.conflicts,
                    how=_how(counts),
                    changed=counts.changed,
                    empty=counts.empty,
                )
            )
    note_user(
        msg.MERGE_TABLE_REPORT(
            table=table_name, rows_in=total.rows_in, rows_out=total.rows_out
        )
    )
    for column, counts in total.columns.items():
        show(
            msg.MERGE_COLUMN_REPORT(
                column=column,
                conflicts=counts.conflicts,
                how=_how(counts),
                changed=counts.changed,
                empty=counts.empty,
            ),
            hang=2,
        )


def check_tables(
    ctx: ValidationContext, tables: object, id_column: str | None = None
) -> bool:
    """Check `tables`, and return whether it is usable.

    It must be a non-empty mapping with usable table names. Each table needs
    a `primary_key` list that includes the id column, if one is given.
    """
    if not isinstance(tables, dict) or len(tables) == 0:
        ctx.fail(Label.TABLES, msg.TABLES_NOT_SETTINGS(value=_wording.shown(tables)))
        return False
    bad_names = [name for name in tables if not PLAIN_NAME.fullmatch(str(name))]
    if bad_names:
        ctx.fail(
            Label.TABLES, msg.TABLE_NAMES_INVALID(names=_wording.listed(bad_names))
        )
        return False
    if id_column is not None:
        for name, config in tables.items():
            if not _keyed_by(config, id_column):
                ctx.fail(
                    Label.TABLES, msg.PRIMARY_KEY_INVALID(table=name, column=id_column)
                )
                return False
    return True


def _keyed_by(config: dict, id_column: str) -> bool:
    """Tell whether a table's settings have a `primary_key` list with the id column."""
    return (
        isinstance(config, dict)
        and isinstance(config.get("primary_key"), list)
        and id_column in config["primary_key"]
    )


def table_columns(config: dict) -> list:
    """List the columns that a table's merge rules name, in order.

    These are the primary key, then every column with a rule. A site's table
    has exactly these columns, with a study_id in place of the id.
    >>> table_columns({"primary_key": ["person_id"], "column": {"score": {}}})
    ['person_id', 'score']
    """
    columns = list(config["primary_key"])
    for rules in column_rules(config):
        columns += [c for c in rules if c not in columns]
    return columns


def column_rules(config: dict) -> list:
    """List a table's column rules: the table's, then each partition's, in order.

    Each entry is a dict, or {} where there are no rules.

    >>> column_rules({"column": {"a": {}}, "partitions": {"p": {}}})
    [{'a': {}}, {}]
    """

    def block(settings: dict) -> dict:
        rules = settings.get("column") if isinstance(settings, dict) else None
        return rules if isinstance(rules, dict) else {}

    partitions = config.get("partitions")
    partitions = partitions.values() if isinstance(partitions, dict) else []
    return [block(config), *map(block, partitions)]


def _check_site_file(
    ctx: ValidationContext,
    site: str,
    data_folder: Path | str,
    provenance: dict,
    run: str | None,
) -> PprlFile | None:
    """Check a site's packed tables. Return the PprlFile, or None.

    The file must be complete, belong to this project and bundle set, and be
    the site's. It must be packed with the study_ids of ``run``, the cleaned
    patient file that the site's linked hashes came from.
    """
    name = file_name("tables", site)
    label = Label.SITE_FILE(site=site)
    site_file = check_pprl_input(
        ctx,
        label,
        name,
        data_folder,
        "tables",
        provenance=provenance,
        site=site,
        lazy=True,
    )
    if (
        site_file is not None
        and run is not None
        and lineage_ids(site_file.header["inputs"], "clean") != {site: run}
    ):
        ctx.fail(label, msg.FILE_OTHER_RUN(name=name, site=site))
        return None
    return site_file


def _linked_person(group_id: pd.Series) -> pd.Series:
    """Name the person that a linked group represents."""
    return "linked" + SEPARATOR + group_id


def _linked_people(linkage_df: pd.DataFrame) -> pd.Series:
    """Map each linked record, keyed by its site and study_id, to its person."""
    return pd.Series(
        _linked_person(linkage_df[GROUP]).to_numpy(),
        index=(linkage_df[SITE] + SEPARATOR + linkage_df[STUDY_ID]).to_numpy(),
    )


def _people(rows: pd.DataFrame, linked: pd.Series, id_column: str) -> pd.Series:
    """Name the person each of a table's ``rows`` belongs to.

    That is the row's linked group (see _linked_people), or, for a record not
    in the linkage file, the record itself.
    """
    record = rows[SITE_COLUMN] + SEPARATOR + rows[id_column]
    return record.map(linked).fillna("record" + SEPARATOR + record)


def _merged_person_ids(people: set[str]) -> dict[str, str]:
    """Map each person to the id they have in every merged table.

    The people are every linked group and every unlinked record, as
    ``_people`` names them. The ids are a random shuffle of 1 to M, drawn
    from the operating system's randomness, so they reveal nothing about the
    sites or their study_ids.
    """
    ordered = sorted(people)
    ids = secrets.SystemRandom().sample(range(1, len(ordered) + 1), len(ordered))
    return dict(zip(ordered, map(str, ids), strict=True))


def _sorted_by_key(
    result: pd.DataFrame, primary_key: list[str], numeric: dict[str, bool]
) -> pd.DataFrame:
    """Sort rows by primary key, comparing numeric columns as numbers.

    ``numeric`` says which columns hold only numbers in the whole table, not
    just in this bucket.
    """
    sort_cols = [c for c in primary_key if c in result.columns]
    keys = {
        col: pd.to_numeric(result[col], errors="coerce")
        if numeric[col]
        else result[col]
        for col in sort_cols
    }
    order = pd.DataFrame(keys).sort_values(sort_cols, kind="stable").index
    return result.loc[order].reset_index(drop=True)


# ---------------------------------------------------------------------------
# The strategies
# ---------------------------------------------------------------------------


def _filled(series: pd.Series) -> pd.Series:
    """Return which values are present, meaning not missing and not empty."""
    return series.notna() & (series != "")


class _Strategy:
    """A ``rank_by`` strategy, identified by its name, with the settings it takes.

    It also has wording for values it cannot compare (``unreadable``, one of
    the VALUES_NOT_* messages). Every strategy except take_first_site names
    the ``column`` it compares.
    """

    name: str
    settings: tuple[str, ...] = ("column",)
    unreadable: Message | None = None

    def problem(
        self,
        settings: dict,  # noqa: ARG002 (subclasses use it)
        column: str,  # noqa: ARG002 (as above)
    ) -> str | None:
        """Describe what is wrong with ``settings``, or return None if nothing is.

        The settings must have the expected names, in the rule for ``column``.
        """
        return

    def ranks(
        self,
        rows: pd.DataFrame,
        settings: dict,
        site_order: list[str],  # noqa: ARG002 (take_first_site uses it)
    ) -> pd.Series:
        """Rank each of ``rows``. A higher rank wins.

        An empty value in the compared column ranks below every other value
        (as NaN, or NaT for dates). So a row without a date counts as older
        than any dated row.
        """
        compared = rows[settings["column"]]
        return self.ranked(compared.where(_filled(compared)), settings)

    def ranked(self, values: pd.Series, settings: dict) -> pd.Series:
        """Return how each of ``values`` ranks. Empty values become missing."""
        raise NotImplementedError

    def comparable(self, values: pd.Series, settings: dict) -> pd.Series:
        """Return which ``values`` the strategy can compare (empty ones are missing)."""
        raise NotImplementedError

    def bad(self, values: pd.Series, settings: dict) -> pd.Series:
        """Return which ``values`` the strategy cannot compare (never empty ones)."""
        filled = _filled(values)
        return filled & ~self.comparable(values.where(filled), settings)


class _TakeHighest(_Strategy):
    """``take_highest: {column: <column>}``: keep the largest number."""

    name = "take_highest"
    unreadable = msg.VALUES_NOT_NUMBERS

    @override
    def ranked(self, values: pd.Series, settings: dict) -> pd.Series:
        return pd.to_numeric(values)

    @override
    def comparable(self, values: pd.Series, settings: dict) -> pd.Series:
        return pd.to_numeric(values, errors="coerce").notna()


class _TakeLatest(_Strategy):
    """``take_latest: {column: <column>}``: keep the latest ISO 8601 date."""

    name = "take_latest"
    unreadable = msg.VALUES_NOT_DATES

    @override
    def ranked(self, values: pd.Series, settings: dict) -> pd.Series:
        return as_iso_8601(values)

    @override
    def comparable(self, values: pd.Series, settings: dict) -> pd.Series:
        return are_iso_8601(values)


class _TakeEarliest(_TakeLatest):
    """``take_earliest: {column: <column>}``: keep the earliest ISO 8601 date.

    A row without a date still ranks below every dated row.
    """

    name = "take_earliest"

    @override
    def ranked(self, values: pd.Series, settings: dict) -> pd.Series:
        dates = as_iso_8601(values)
        return -(dates - dates.min())


class _TakeTopOrdinal(_Strategy):
    """``take_top_ordinal: {column: <column>, scale: [<value>, ...]}``.

    Keep the highest value on the scale. The scale lists values lowest first.
    """

    name = "take_top_ordinal"
    settings = ("column", "scale")
    unreadable = msg.VALUES_NOT_IN_SCALE

    @override
    def problem(self, settings: dict, column: str) -> str | None:
        scale = settings["scale"]
        if not isinstance(scale, list) or not scale:
            return msg.SCALE_MISSING(column=column)
        return None

    @override
    def ranked(self, values: pd.Series, settings: dict) -> pd.Series:
        scale = _scale(settings)
        return values.map(lambda v: scale.index(v) if v in scale else float("nan"))

    @override
    def comparable(self, values: pd.Series, settings: dict) -> pd.Series:
        return values.isin(_scale(settings))


class _TakeFirstSite(_Strategy):
    """``take_first_site: {}``: keep the first site, in the order of ``sites``.

    This strategy compares no column.
    """

    name = "take_first_site"
    settings = ()

    @override
    def ranks(
        self, rows: pd.DataFrame, settings: dict, site_order: list[str]
    ) -> pd.Series:
        return -rows[SITE_COLUMN].map(site_order.index)


# Every strategy, keyed by its name.
STRATEGIES = {
    strategy.name: strategy
    for strategy in (
        _TakeHighest(),
        _TakeLatest(),
        _TakeEarliest(),
        _TakeTopOrdinal(),
        _TakeFirstSite(),
    )
}


def _scale(settings: dict) -> list[str]:
    """Return a take_top_ordinal's scale as text, which is how every value is read."""
    return [str(v) for v in settings["scale"]]


def _strategy(item: dict) -> tuple[_Strategy, dict]:
    """Read a checked ``rank_by`` item, returning its strategy and settings.

    The strategy is one of STRATEGIES.
    """
    ((name, settings),) = item.items()
    return STRATEGIES[name], settings


def _settings_problem(
    column: str, name: str, strategy: _Strategy, settings: dict, columns: list[str]
) -> str | None:
    """Describe what is wrong with the ``settings`` of the strategy ``name``.

    Return None if nothing is wrong (see _ranking_problem).
    """
    if not strategy.settings:
        if settings != {}:
            return msg.STRATEGY_NO_SETTINGS(column=column, strategy=name)
        return None
    if not isinstance(settings, dict) or set(map(str, settings)) != set(
        strategy.settings
    ):
        return msg.STRATEGY_SETTINGS(
            column=column, strategy=name, settings=_wording.listed(strategy.settings)
        )
    compared = settings["column"]
    if compared not in columns:
        return msg.RANK_COLUMN_NOT_IN_DATA(
            column=column, strategy=name, compared=compared
        )
    return strategy.problem(settings, column)


def _ranking_problem(
    column: str, position: int, item: object, columns: list[str]
) -> str | None:
    """Describe what is wrong with an item of a ``rank_by``.

    The item is at ``position`` in the ``rank_by`` of ``column``. Return None
    if nothing is wrong. A strategy that compares a column must compare one
    of ``columns``.
    """
    if not isinstance(item, dict) or len(item) != 1:
        return msg.RANK_BY_ITEM(column=column, position=position)
    ((name, settings),) = item.items()
    strategy = STRATEGIES.get(name)
    if strategy is None:
        return msg.STRATEGY_UNKNOWN(
            column=column,
            strategy=_wording.shown(name),
            strategies=_wording.listed(STRATEGIES),
        )
    return _settings_problem(column, name, strategy, settings, columns)


# ---------------------------------------------------------------------------
# Merging one table
# ---------------------------------------------------------------------------


def _criteria(rank_by: list) -> list[tuple[_Strategy, dict]]:
    """Return ``rank_by`` as ``(strategy, settings)`` pairs, in order.

    Each strategy is one of STRATEGIES.
    """
    return list(map(_strategy, rank_by))


# What decided a merged row's value when the sites' values differed: either
# the strategy's position in its rank_by, or the order of the sites.
BY_SITES = "sites"


def _winners(
    df: pd.DataFrame,
    groups: pd.Series,
    column: str,
    criteria: list[tuple[_Strategy, dict]],
    site_order: list[str],
) -> tuple[pd.Series, pd.Series]:
    """Find the row whose ``column`` value wins in each group, and why.

    ``groups`` gives each row's group number. Return two Series, keyed by
    group. The first gives the index label of each group's winning row; a
    group with no values at all has none. The second says, for each group
    whose values differ, what decided it (see BY_SITES).

    Only rows with a value in ``column`` take part. Each strategy in turn
    keeps the rows it ranks highest, in every group at once, so a tie under
    one strategy goes to the next. Rows that a strategy cannot rank at all
    stay tied. A tie under every strategy goes to the first site in
    ``site_order``, then to the first row.
    """
    filled = _filled(df[column])
    rows, keys = df[filled], groups[filled]
    differ = rows[column].groupby(keys).nunique() > 1  # noqa: PD101 (per group)
    decided = pd.Series(BY_SITES, index=differ.index[differ], dtype=object)
    for position, (strategy, settings) in enumerate(criteria):
        ranks = strategy.ranks(rows, settings, site_order)
        best = ranks.groupby(keys).transform("max")
        kept = best.isna() | (ranks == best)
        rows, keys = rows[kept], keys[kept]
        agreed = rows[column].groupby(keys).nunique() == 1  # noqa: PD101 (per group)
        settled = agreed.index[agreed].intersection(decided.index)
        settled = settled[decided[settled] == BY_SITES]
        decided[settled] = position
    order = pd.DataFrame(
        {
            "group": keys.to_numpy(),
            "site": rows[SITE_COLUMN].map({s: i for i, s in enumerate(site_order)}),
            "position": range(len(rows)),
        },
        index=rows.index,
    ).sort_values(["group", "site", "position"], kind="stable")
    first = ~order["group"].duplicated()
    winners = pd.Series(order.index[first], index=order["group"][first].to_numpy())
    return winners, decided


def _lead_rules(column_config: dict) -> dict:
    """List the columns that have ``rank_by``, each with the columns tethered to it."""
    return {
        name: {
            "criteria": _criteria(spec["rank_by"]),
            "followers": [
                f for f, fs in column_config.items() if fs.get("tether") == name
            ],
        }
        for name, spec in column_config.items()
        if "rank_by" in spec and "tether" not in spec
    }


@dataclass
class ColumnCounts:
    """What merging decided for one column of a table or partition.

    - ``conflicts``: merged rows whose sites had different values.
    - ``changed``: sites' values that differ from the merged row's value.
    - ``empty``: merged rows with no value.
    - ``decided``: for a column with rank_by, what decided each conflict
      (see BY_SITES).
    - ``lead``: for a tethered column, the column whose row its value
      comes from.
    """

    lead: str | None
    conflicts: int = 0
    changed: int = 0
    empty: int = 0
    decided: dict = field(default_factory=dict)

    def add(self, other: ColumnCounts) -> None:
        """Add the counts from ``other``, the same column in another partition."""
        self.conflicts += other.conflicts
        self.changed += other.changed
        self.empty += other.empty
        for reason, count in other.decided.items():
            self.decided[reason] = self.decided.get(reason, 0) + count


@dataclass
class PartCounts:
    """What merging one table, or one partition of it, did.

    It holds the rows read and written, and each column's counts.
    """

    rows_in: int = 0
    rows_out: int = 0
    columns: dict = field(default_factory=dict)

    def add(self, other: PartCounts) -> None:
        """Add the counts from ``other``.

        ``other`` covers another bucket of the same rows, or another partition.
        """
        self.rows_in += other.rows_in
        self.rows_out += other.rows_out
        for column, counts in other.columns.items():
            self.columns.setdefault(column, ColumnCounts(counts.lead)).add(counts)


def _criterion(position: object, criteria: Sequence[tuple[_Strategy, dict]]) -> str:
    """Describe what decided a conflict.

    That is a strategy in ``criteria``, given by its position, or the order
    of the sites.
    """
    if not isinstance(position, int):  # BY_SITES
        return msg.MERGE_BY_SITES
    strategy, settings = criteria[position]
    if "column" not in strategy.settings:
        return strategy.name
    return msg.MERGE_BY_STRATEGY(strategy=strategy.name, column=settings["column"])


def _counted(
    df: pd.DataFrame,
    groups: pd.Series,
    column: str,
    merged: pd.Series,
    lead: str | None,
    *,
    decided: pd.Series | None = None,
    criteria: Sequence[tuple[_Strategy, dict]] = (),
) -> ColumnCounts:
    """Count what merging one column did.

    The column's rule is ``criteria``, and ``decided`` records what decided
    each conflict. Alternatively, the column is tethered to ``lead``.
    ``merged`` holds each group's value.
    """
    filled = _filled(df[column])
    values, keys = df.loc[filled, column], groups[filled].to_numpy()
    kept = merged.to_numpy()[keys]
    counts = ColumnCounts(
        lead=lead,
        conflicts=int((values.groupby(keys).nunique() > 1).sum()),  # noqa: PD101 (per group)
        changed=int((values.to_numpy() != kept).sum()),
        empty=int((merged == "").sum()),
    )
    if decided is not None:
        for position, count in decided.value_counts().items():
            counts.add(
                ColumnCounts(None, decided={_criterion(position, criteria): count})
            )
    return counts


def merge_table(
    df: pd.DataFrame, table_config: dict, site_order: list[str], id_column: str
) -> tuple[pd.DataFrame, dict[str | None, PartCounts]]:
    """Merge a single table's combined rows from every site.

    ``check_table`` has already checked the data and config.

    Parameters
    ----------
    df : DataFrame
        Every site's rows, with GROUP_COLUMN and SITE_COLUMN.
    table_config : dict
        The table's merge rules, from the bundle.
    site_order : list[str]
        The sites, in tie-breaking order.
    id_column : str
        The id column of the primary key, whose values are GROUP_COLUMN's.

    Returns
    -------
    DataFrame
        Merged output.
    dict
        What merging did (a PartCounts). For an unpartitioned table, the key
        is None. Otherwise there is one entry for each partition value that
        has rows.
    """
    primary_key = table_config["primary_key"]
    partition_by = table_config.get("partition_by")
    report = {}
    if not partition_by:
        column_config = table_config.get("column", {})
        result, report[None] = _merge_simple_table(
            df, column_config, primary_key, site_order, id_column
        )
        return result, report

    partition_results = []
    for partition_value, partition_spec in table_config["partitions"].items():
        partition_df = df[df[partition_by] == partition_value]
        if len(partition_df) == 0:
            continue
        column_config = partition_spec.get("column", {})
        result, report[partition_value] = _merge_simple_table(
            partition_df, column_config, primary_key, site_order, id_column
        )
        partition_results.append(result)
    if not partition_results:
        return pd.DataFrame(), report
    return pd.concat(partition_results, ignore_index=True), report


def _merge_simple_table(
    df: pd.DataFrame,
    column_config: dict,
    primary_key: list[str],
    site_order: list[str],
    id_column: str,
) -> tuple[pd.DataFrame, PartCounts]:
    """Merge a table, or one partition of it, with one set of column rules.

    Return the merged rows, one per primary-key value, in the order each
    value first appears. Also return what merging did (a PartCounts).
    """
    group_cols = [GROUP_COLUMN if c == id_column else c for c in primary_key]
    groups = df.groupby(group_cols, sort=False).ngroup()
    result = df.loc[~groups.duplicated(), group_cols].reset_index(drop=True)
    counts = PartCounts(rows_in=len(df), rows_out=len(result), columns={})

    for lead, rule in _lead_rules(column_config).items():
        winners, decided = _winners(df, groups, lead, rule["criteria"], site_order)
        for column in (lead, *rule["followers"]):
            if column_config[column].get("drop", False):
                continue
            merged = pd.Series("", index=result.index, dtype=object)
            merged[winners.index] = df.loc[winners.to_numpy(), column].to_numpy()
            result[column] = merged
            if column == lead:
                found = _counted(
                    df,
                    groups,
                    column,
                    merged,
                    None,
                    decided=decided,
                    criteria=rule["criteria"],
                )
            else:
                found = _counted(df, groups, column, merged, lead)
            counts.columns[column] = found

    result = result.rename(columns={GROUP_COLUMN: id_column})
    columns = merged_columns(primary_key, [column_config])
    return result[[c for c in columns if c in result.columns]], counts


# ---------------------------------------------------------------------------
# Checking one table's config against its data
# ---------------------------------------------------------------------------


class TableFacts:
    """What the checks of a table's rules need to know about its rows.

    The facts are gathered one chunk at a time (``add``), so a table is
    never held in memory all at once. They are the table's columns and
    rows, its partitions, and, for each comparison its rules make (see
    _comparisons), the values the strategy cannot compare.
    """

    def __init__(
        self, columns: list[str], config: dict, partition_by: str | None = None
    ) -> None:
        self.columns, self.partition_by = list(columns), partition_by
        self.rows = self.empty = 0
        self.values = set()  # The non-empty partition values
        self.bad = {}
        for scope, strategy, settings in _comparisons(
            config, self.columns, partition_by
        ):
            key = _compared(scope, strategy, settings)
            self.bad.setdefault(key, _Uncompared(strategy, settings))

    def add(self, chunk: pd.DataFrame) -> None:
        """Add a chunk of rows from every site (with SITE_COLUMN)."""
        self.rows += len(chunk)
        if self.partition_by:
            filled = _filled(chunk[self.partition_by])
            self.empty += int((~filled).sum())
            self.values.update(chunk.loc[filled, self.partition_by])
        for (scope, *_), found in self.bad.items():
            rows = chunk
            if scope is not None:
                rows = chunk[chunk[self.partition_by] == scope]
            compared = found.settings["column"]
            if compared not in rows.columns:
                continue
            bad = found.strategy.bad(rows[compared], found.settings)
            found.count += int(bad.sum())
            for site in rows.loc[bad, SITE_COLUMN].unique():
                if site not in found.sites:
                    found.sites.append(site)

    def whole(self) -> _PartFacts:
        """Return the facts of the whole table."""
        return _PartFacts(self, None)

    def part(self, scope: str | None) -> _PartFacts | None:
        """Return the facts of one partition, or of the whole table for None.

        Return None for a partition with no rows.
        """
        if scope is not None and scope not in self.values:
            return None
        return _PartFacts(self, scope)


@dataclass
class _Uncompared:
    """The values that one comparison in a table's rules cannot compare.

    It holds how many there are, and their sites in the order found.
    """

    strategy: _Strategy
    settings: dict
    count: int = 0
    sites: list = field(default_factory=list)


class _PartFacts:
    """The facts of one partition of a table, or of the whole table."""

    def __init__(self, facts: TableFacts, scope: str | None) -> None:
        self.facts, self.scope, self.columns = facts, scope, facts.columns

    def bad(self, strategy: _Strategy, settings: dict) -> tuple[int, list[str]]:
        """Count the values ``strategy`` cannot compare, and list their sites.

        The sites are in the order they were found.
        """
        found = self.facts.bad[_compared(self.scope, strategy, settings)]
        return found.count, found.sites


def _compared(scope: str | None, strategy: _Strategy, settings: dict) -> tuple:
    """Return the key for what a comparison compares, in a partition (or None).

    Rules that compare the same thing share one key, so their uncomparable
    values are counted once.
    """
    scale = tuple(_scale(settings)) if "scale" in settings else ()
    return scope, strategy.name, settings["column"], scale


def _comparisons(
    config: dict, columns: list[str], partition_by: str | None
) -> Iterator[tuple]:
    """List every comparison in a table's rules whose values the checks count.

    Each comparison is (partition or None, strategy, settings); see
    _check_rank_by. ``partition_by`` is the table's partition column, if it
    is one of ``columns``. The list may include rules that the checks find
    wrong for other reasons.
    """
    if not isinstance(config, dict):
        return
    parts = {None: config.get("column")}
    if partition_by:
        partitions = config.get("partitions")
        partitions = partitions if isinstance(partitions, dict) else {}
        parts = {
            value: spec.get("column")
            for value, spec in partitions.items()
            if isinstance(spec, dict)
        }
    for scope, column_config in parts.items():
        if not isinstance(column_config, dict):
            continue
        for column, spec in column_config.items():
            rank_by = spec.get("rank_by") if isinstance(spec, dict) else None
            if not isinstance(rank_by, list):
                continue
            for position, item in enumerate(rank_by, start=1):
                if _ranking_problem(column, position, item, columns) is None:
                    strategy, settings = _strategy(item)
                    if strategy.unreadable is not None:
                        yield scope, strategy, settings


def _checked_key(
    ctx: ValidationContext,
    label: str,
    table_name: str,
    table_config: object,
    *,
    facts: TableFacts,
    id_column: str,
) -> list[str] | None:
    """Check and return the primary key from a table's settings.

    Check its keys, and find its columns in ``facts``. Return None, after
    recording a failure, if the check fails.
    """
    if not isinstance(table_config, dict):
        ctx.fail(label, msg.TABLE_NOT_SETTINGS(table=table_name))
        return None
    unknown = sorted(map(str, table_config.keys() - _TABLE_KEYS))
    if unknown:
        ctx.fail(
            label,
            msg.TABLE_UNKNOWN_KEYS(
                table=table_name,
                keys=unknown,
                allowed=_wording.listed(sorted(_TABLE_KEYS)),
            ),
        )
        return None

    primary_key = table_config.get("primary_key")
    if not isinstance(primary_key, list) or id_column not in primary_key:
        ctx.fail(label, msg.PRIMARY_KEY_INVALID(table=table_name, column=id_column))
        return None
    missing = [c for c in primary_key if c != id_column and c not in facts.columns]
    if missing:
        ctx.fail(
            label,
            msg.PRIMARY_KEY_NOT_IN_DATA(table=table_name, columns=missing),
        )
        return None
    return primary_key


def check_table(
    ctx: ValidationContext,
    table_name: str,
    table_config: object,
    facts: TableFacts,
    id_column: str,
) -> None:
    """Check a table's config against its actual data columns.

    Anything the config leaves unclear is an error: an unknown key, a column
    with no rule, a strategy that does not exist, or a value the strategy
    cannot compare.

    Parameters
    ----------
    ctx : ValidationContext
        Collects the results.
    table_name : str
        Table name for error messages.
    table_config : dict
        Table configuration dict.
    facts : TableFacts
        What every site's rows hold, with SITE_COLUMN, and with their
        study_ids as id_column.
    id_column : str
        The id column, which every primary key includes.
    """
    label = Label.TABLE(table=table_name)
    primary_key = _checked_key(
        ctx, label, table_name, table_config, facts=facts, id_column=id_column
    )
    if primary_key is None or not isinstance(table_config, dict):  # Failed above
        return
    has_column = "column" in table_config
    has_partition = "partition_by" in table_config or "partitions" in table_config
    if has_column and has_partition:
        ctx.fail(label, msg.COLUMN_AND_PARTITIONS(table=table_name))
        return

    partition_by = table_config.get("partition_by")
    if not partition_by:
        column_config = table_config.get("column", {})
        _check_columns(
            ctx,
            table_name,
            table_name,
            column_config,
            primary_key,
            part=facts.whole(),
            id_column=id_column,
        )
        return
    if partition_by not in primary_key:
        ctx.fail(
            label, msg.PARTITION_BY_NOT_IN_KEY(table=table_name, column=partition_by)
        )
        return
    _check_partitions(ctx, table_name, table_config, facts, id_column)


def _fail_rows(
    ctx: ValidationContext,
    label: str,
    message: Message,
    count: int,
    sites: list[str],
    *,
    table_name: str,
    **values: object,
) -> None:
    """Fail with ``message`` if any row is bad.

    Show the ``count`` of bad rows and the ``sites`` whose files hold them.
    Never show a value or a study_id.
    """
    if not count:
        return
    ctx.fail(
        label,
        message(
            count=count,
            where=_wording.listed(
                msg.ROWS_IN_TABLE(name=file_name("tables", site), table=table_name)
                for site in sites
            ),
            **values,
        ),
    )


def _check_columns(
    ctx: ValidationContext,
    table_name: str,
    part_name: str,
    column_config: dict,
    primary_key: list[str],
    *,
    part: _PartFacts,
    id_column: str,
) -> None:
    """Check the column declarations against the actual data.

    ``part_name`` names the table, or the table and partition, for messages.
    """
    label = Label.TABLE(table=part_name)
    if not isinstance(column_config, dict):
        ctx.fail(label, msg.COLUMNS_NOT_SETTINGS(table=part_name))
        return
    internal_cols = {SITE_COLUMN, GROUP_COLUMN}
    data_cols = set(part.columns) - set(primary_key) - internal_cols
    data_cols.discard(id_column)
    declared_cols = set(column_config.keys())

    undeclared = data_cols - declared_cols
    if undeclared:
        ctx.fail(
            label,
            msg.COLUMNS_UNDECLARED(table=part_name, columns=sorted(undeclared)),
        )
    absent = declared_cols - data_cols
    if absent:
        ctx.fail(
            label,
            msg.COLUMNS_NOT_IN_DATA(table=part_name, columns=sorted(map(str, absent))),
        )

    for col_name, col_spec in column_config.items():
        _check_column(
            ctx,
            Label.TABLE_COLUMN(table=part_name, column=col_name),
            col_name,
            col_spec,
            column_config,
            part=part,
            table_name=table_name,
        )

    _check_circular_tethers(ctx, part_name, column_config)


def _check_column(
    ctx: ValidationContext,
    label: str,
    col_name: str,
    col_spec: object,
    column_config: dict,
    *,
    part: _PartFacts,
    table_name: str,
) -> None:
    """Check one column's rule (``col_spec``) in a table's ``column_config``."""
    if not isinstance(col_spec, dict):
        ctx.fail(label, msg.COLUMN_RULE_NOT_SETTINGS(column=col_name))
        return
    unknown = sorted(map(str, col_spec.keys() - _COLUMN_KEYS))
    if unknown:
        ctx.fail(
            label,
            msg.COLUMN_RULE_UNKNOWN_KEYS(
                column=col_name,
                keys=unknown,
                allowed=_wording.listed(sorted(_COLUMN_KEYS)),
            ),
        )
        return
    rank_by = col_spec.get("rank_by")
    tether_target = col_spec.get("tether")
    if rank_by is None and tether_target is None and not col_spec.get("drop"):
        ctx.fail(label, msg.COLUMN_NO_RULE(column=col_name))
    if rank_by is not None:
        _check_rank_by(ctx, label, col_name, rank_by, part, table_name=table_name)
    if tether_target is not None:
        if not isinstance(tether_target, str) or tether_target not in column_config:
            ctx.fail(
                label,
                msg.TETHER_UNKNOWN(
                    column=col_name, target=_wording.shown(tether_target)
                ),
            )
        elif not _has_rank_by(column_config[tether_target]):
            ctx.fail(
                label,
                msg.TETHER_TARGET_NO_STRATEGY(column=col_name, target=tether_target),
            )
        if rank_by is not None:
            ctx.fail(label, msg.TETHER_WITH_STRATEGY(column=col_name))


def _check_rank_by(
    ctx: ValidationContext,
    label: str,
    column: str,
    rank_by: object,
    part: _PartFacts,
    *,
    table_name: str,
) -> None:
    """Check each strategy in a column's ``rank_by`` against the data."""
    if not isinstance(rank_by, list) or not rank_by:
        ctx.fail(label, msg.RANK_BY_NOT_LIST(column=column))
        return
    for position, item in enumerate(rank_by, start=1):
        problem = _ranking_problem(column, position, item, part.columns)
        if problem is not None:
            ctx.fail(label, problem)
            continue
        strategy, settings = _strategy(item)
        if strategy.unreadable is not None:
            count, sites = part.bad(strategy, settings)
            _fail_rows(
                ctx,
                label,
                strategy.unreadable,
                count,
                sites,
                table_name=table_name,
                strategy=strategy.name,
                column=settings["column"],
            )


def _has_rank_by(spec: object) -> bool:
    """Tell whether a column's rule has ``rank_by``. The rule may not be a dict."""
    return isinstance(spec, dict) and "rank_by" in spec


def _tether_of(column_config: dict, column: str) -> str | None:
    """Return the column that ``column`` is tethered to, or None.

    Also return None for a column with no rule, or with a rule that is not a
    dict (which _check_columns reports).
    """
    spec = column_config.get(column)
    target = spec.get("tether") if isinstance(spec, dict) else None
    return target if isinstance(target, str) else None


def _check_circular_tethers(
    ctx: ValidationContext, part_name: str, column_config: dict
) -> None:
    """Report each column in a loop of tethers.

    Only the first loop found is reported. Once it is fixed, the next run
    reports the next loop.
    """
    tethers = {
        column: [target]
        for column in column_config
        if (target := _tether_of(column_config, column)) is not None
    }
    try:
        TopologicalSorter(tethers).prepare()
    except CycleError as error:
        loop = set(error.args[1])
        for column in column_config:
            if column in loop:
                ctx.fail(
                    Label.TABLE_COLUMN(table=part_name, column=column),
                    msg.TETHER_CIRCULAR(column=column),
                )


def _check_partitions(
    ctx: ValidationContext,
    table_name: str,
    table_config: dict,
    facts: TableFacts,
    id_column: str,
) -> None:
    """Check the partition declarations against the actual data."""
    label = Label.TABLE(table=table_name)
    partition_by = table_config["partition_by"]
    partitions_config = table_config.get("partitions", {})
    primary_key = table_config["primary_key"]

    if not partitions_config:
        ctx.fail(label, msg.PARTITIONS_MISSING(table=table_name))
        return

    if facts.empty:
        ctx.fail(
            label,
            msg.PARTITION_VALUES_EMPTY(
                table=table_name, count=facts.empty, column=partition_by
            ),
        )
    undeclared = facts.values - set(partitions_config.keys())  # noqa: PD011 (a set)
    if undeclared:
        ctx.fail(
            label,
            msg.PARTITIONS_UNDECLARED(
                table=table_name, count=len(undeclared), column=partition_by
            ),
        )

    for partition_value, partition_spec in partitions_config.items():
        part = facts.part(partition_value)
        if part is None:
            continue
        _check_columns(
            ctx,
            table_name,
            Label.PARTITION(table=table_name, value=partition_value),
            partition_spec.get("column", {}),
            primary_key,
            part=part,
            id_column=id_column,
        )
