"""Test merge: conflict resolution, tethers, partitions and person ids."""

import random
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import numpy as np
import pandas as pd
import pytest

from pprl import _merge, _pprl_file
from pprl import _messages as msg
from pprl._io import read_csv_frame, read_text
from pprl._merge import (
    GROUP_COLUMN,
    SITE_COLUMN,
    ColumnCounts,
    PartCounts,
    inconsistency,
    merge_table,
)
from pprl._pprl_file import file_name
from tests import merge_reference
from tests.helpers import SITES, provenance, seeds, shows, strip_ansi

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pprl._pprl_file import PprlFile
    from pprl._wording import Message

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class MergeCase:
    """A merge to run in ``folder``, built up one step at a time.

    The steps are: the records that link joined (``link``), each site's
    tables (``table``), then the merge rules and the order of the sites
    (``run``), and finally what merge wrote (``output``). The id column is
    person_id. By default the sites are site_a and site_b, in that order,
    and the output folder is output.

    When the merge runs, the linkages are written into linkages.pprl, as
    pprl link writes them. Each site's tables are written into
    site_X.tables.pprl, as pprl pack writes them; a site with no tables
    gets an empty file. A file that already exists is kept. The linkage file
    names no inputs, so merge has no clean run to compare.
    """

    def __init__(self, folder: Path, sites: Iterable[str] = SITES) -> None:
        self.folder = Path(folder)
        self.sites = list(sites)
        self.linked = []
        self.tables = {site: {} for site in self.sites}

    def link(self, *records: tuple[str, str, str]) -> MergeCase:
        """Add the records that link joined, each as (group_id, site, study_id)."""
        self.linked += records
        return self

    def table(
        self,
        name: str,
        site: str,
        rows: pd.DataFrame | None = None,
        /,
        **columns: object,
    ) -> MergeCase:
        """Add the table ``name`` for the site ``site``.

        Give ``rows`` as a DataFrame, or give each column's values as a list,
        or as a single value for a one-row table.
        """
        if rows is None:
            lists = [isinstance(v, list) for v in columns.values()]
            rows = pd.DataFrame(columns, index=None if any(lists) else [0])
        self.tables[site][name] = rows.astype(str)
        return self

    @property
    def linkage_rows(self) -> pd.DataFrame:
        """Return the linkages as pprl link writes them: group_id, site, study_id."""
        return pd.DataFrame(self.linked, columns=["group_id", "site", "study_id"])

    def run(
        self, tables: dict, *, sites: list[str] | None = None, output: str = "output"
    ) -> int:
        """Merge with the rules in ``tables``, into the folder ``output``.

        The sites are in the order given, or the case's order by default.
        Return the exit code.
        """
        linkage_file = self.folder / file_name("linkages")
        if not linkage_file.exists():
            members = {"linkages": self.linkage_rows}
            _pprl_file.write(linkage_file, "linkages", members, provenance=provenance())
        for site, members in self.tables.items():
            tables_file = self.folder / file_name("tables", site)
            if not tables_file.exists():
                _pprl_file.write(
                    tables_file, "tables", members, provenance=provenance(site)
                )
        return _merge.merge(
            linkages=file_name("linkages"),
            sites=self.sites if sites is None else sites,
            id_column="person_id",
            tables=tables,
            output=output,
            provenance=provenance(),
            data_folder=self.folder,
            output_folder=self.folder,
        )

    def output(self, name: str, output: str = "output") -> pd.DataFrame:
        """Read the merged table ``name``, in the folder ``output``."""
        return read_csv_frame(self.folder / output / f"{name}.csv")


def ranked(strategy: str, **settings: object) -> dict:
    """Make a column rule that ranks by one strategy, with its settings."""
    return {"rank_by": [{strategy: settings}]}


def by_person(columns: dict, *key: str) -> dict:
    """Make a table's rules: its primary key and each column's rule.

    The primary key is person_id followed by ``key``.
    """
    return {"primary_key": ["person_id", *key], "column": columns}


# ---------------------------------------------------------------------------
# Strategy tests
# ---------------------------------------------------------------------------

# One linked person: study_id p1 at site_a and p2 at site_b.
ONE_PERSON = (("1", "site_a", "p1"), ("1", "site_b", "p2"))


@pytest.mark.reliability
class TestMergeStrategies:
    """Merge strategies resolve conflicts correctly."""

    def test_take_highest_numeric(self, tmp_path: Path) -> None:
        """take_highest selects the maximum numeric value."""
        case = MergeCase(tmp_path).link(*ONE_PERSON)
        case.table("scores", "site_a", study_id="p1", score="5")
        case.table("scores", "site_b", study_id="p2", score="10")
        rules = {"score": ranked("take_highest", column="score")}
        assert case.run({"scores": by_person(rules)}) == 0
        assert case.output("scores")["score"].iloc[0] == "10"

    def test_take_latest(self, tmp_path: Path) -> None:
        """take_latest selects the value from the row with the most recent date."""
        case = MergeCase(tmp_path).link(*ONE_PERSON)
        case.table(
            "visits",
            "site_a",
            study_id="p1",
            location="clinic_a",
            visit_date="2023-01-01",
        )
        case.table(
            "visits",
            "site_b",
            study_id="p2",
            location="clinic_b",
            visit_date="2024-06-15",
        )
        rules = {
            "location": ranked("take_latest", column="visit_date"),
            "visit_date": {"tether": "location"},
        }
        assert case.run({"visits": by_person(rules)}) == 0
        result = case.output("visits")
        assert result["location"].iloc[0] == "clinic_b"
        assert result["visit_date"].iloc[0] == "2024-06-15"

    def test_take_earliest(self, tmp_path: Path) -> None:
        """take_earliest selects the value from the row with the oldest date.

        A row without a date still loses to any dated row.
        """
        case = MergeCase(tmp_path).link(
            ("1", "site_a", "p1"),
            ("1", "site_b", "p2"),
            ("2", "site_a", "p3"),
            ("2", "site_b", "p4"),
        )
        case.table(
            "vals",
            "site_a",
            study_id=["p1", "p3"],
            value=["late", "undated"],
            date=["2024-06-15", ""],
        )
        case.table(
            "vals",
            "site_b",
            study_id=["p2", "p4"],
            value=["early", "dated"],
            date=["2023-01-01", "2025-01-01"],
        )
        rules = {
            "value": ranked("take_earliest", column="date"),
            "date": {"tether": "value"},
        }
        assert case.run({"vals": by_person(rules)}) == 0
        assert sorted(case.output("vals")["value"]) == ["dated", "early"]

    def test_take_top_ordinal(self, tmp_path: Path) -> None:
        """take_top_ordinal selects the highest-ranked value from the scale."""
        case = MergeCase(tmp_path).link(*ONE_PERSON)
        case.table("measures", "site_a", study_id="p1", status="not_met")
        case.table("measures", "site_b", study_id="p2", status="met")
        scale = ["missing", "not_met", "met"]
        rules = {"status": ranked("take_top_ordinal", column="status", scale=scale)}
        assert case.run({"measures": by_person(rules)}) == 0
        assert case.output("measures")["status"].iloc[0] == "met"

    def test_three_site_merge(self, tmp_path: Path) -> None:
        """A ranked column is merged across three sites, not just two."""
        sites = ["site_a", "site_b", "site_c"]
        case = MergeCase(tmp_path, sites)
        for site, pid, score in zip(
            sites, ["p1", "p2", "p3"], ["30", "35", "32"], strict=True
        ):
            case.link(("1", site, pid)).table("scores", site, study_id=pid, score=score)
        rules = {"score": ranked("take_highest", column="score")}
        assert case.run({"scores": by_person(rules)}) == 0
        assert case.output("scores")["score"].iloc[0] == "35"


# ---------------------------------------------------------------------------
# Tie-breaking tests
# ---------------------------------------------------------------------------


@pytest.mark.reliability
class TestTieBreaking:
    """Ties are broken by the order of the sites."""

    def _tied(self, tmp_path: Path, sites: list[str]) -> str:
        """Return the label merged from a tie between site_a and site_b.

        The sites are in the order ``sites``.
        """
        case = MergeCase(tmp_path).link(*ONE_PERSON)
        case.table("data", "site_a", study_id="p1", value="10", label="from_a")
        case.table("data", "site_b", study_id="p2", value="10", label="from_b")
        rules = {
            "value": ranked("take_highest", column="value"),
            "label": {"tether": "value"},
        }
        assert case.run({"data": by_person(rules)}, sites=sites) == 0
        return case.output("data")["label"].iloc[0]

    def test_tie_broken_by_site_order(self, tmp_path: Path) -> None:
        """When values are equal, the first site in the list wins."""
        assert self._tied(tmp_path, ["site_a", "site_b"]) == "from_a"

    def test_reversed_site_order(self, tmp_path: Path) -> None:
        """Changing the order of the sites changes tie-breaking."""
        assert self._tied(tmp_path, ["site_b", "site_a"]) == "from_b"


# ---------------------------------------------------------------------------
# Tethered columns
# ---------------------------------------------------------------------------


@pytest.mark.reliability
class TestRankBy:
    """A column ranked by several strategies.

    Each strategy breaks the ties of the one before it, and each compares
    the column it names.
    """

    def _merge_status(
        self, tmp_path: Path, rows: Iterable[tuple[str, str, str]], rank_by: list
    ) -> tuple[str, str]:
        """Merge one person's ``(site, status, date)`` rows; return the winner."""
        case = MergeCase(tmp_path, [site for site, _, _ in rows])
        for site, status, date in rows:
            case.link(("1", site, "p1"))
            case.table("results", site, study_id="p1", status=status, date=date)
        rules = {"status": {"rank_by": rank_by}, "date": {"tether": "status"}}
        assert case.run({"results": by_person(rules)}) == 0, "merge failed"
        result = case.output("results")
        return result["status"].iloc[0], result["date"].iloc[0]

    LATEST_THEN_VALUE: ClassVar = [
        {"take_latest": {"column": "date"}},
        {"take_top_ordinal": {"column": "status", "scale": ["-1", "0", "1"]}},
    ]

    def test_later_strategy_breaks_ties(self, tmp_path: Path) -> None:
        """The first strategy decides, and the next one decides only its ties.

        The second strategy only chooses between rows tied under the first,
        before the order of the sites is used.
        """
        cases = {
            "latest wins": (
                [("a", "1", "2024-03-01"), ("b", "0", "2024-06-01")],
                ("0", "2024-06-01"),
            ),
            "same date, value decides": (
                [("a", "0", "2024-06-01"), ("b", "1", "2024-06-01")],
                ("1", "2024-06-01"),
            ),
            "no dates, value decides": (
                [("a", "-1", ""), ("b", "0", ""), ("c", "1", "")],
                ("1", ""),
            ),
            "dated row beats undated": (
                [("a", "1", ""), ("b", "-1", "2024-01-01")],
                ("-1", "2024-01-01"),
            ),
            "full tie, first site": (
                [("a", "0", "2024-06-01"), ("b", "0", "2024-06-01")],
                ("0", "2024-06-01"),
            ),
        }
        for name, (rows, expected) in cases.items():
            folder = tmp_path / name.replace(" ", "_").replace(",", "")
            folder.mkdir()
            winner = self._merge_status(folder, rows, self.LATEST_THEN_VALUE)
            assert winner == expected, f"{name}: {winner}, not {expected}"

    def test_compares_the_named_column(self, tmp_path: Path) -> None:
        """A strategy compares the column it names, and an empty value ranks last.

        That column need not be the one it chooses a value for.
        """
        rows = [("a", "low", "2024-06-01"), ("b", "high", ""), ("c", "mid", "")]
        rank_by = [{"take_latest": {"column": "date"}}]
        assert self._merge_status(tmp_path, rows, rank_by) == ("low", "2024-06-01")

    def test_take_first_site_fills_gaps(self, tmp_path: Path) -> None:
        """take_first_site takes each value from the first site that has one.

        Sites are taken in the order of `sites`, and tethered columns come
        along with the value.
        """
        rows = {
            "a": {"label": "", "region": "", "code": ""},
            "b": {"label": "from b", "region": "", "code": "b's code"},
            "c": {"label": "from c", "region": "from c", "code": "c's code"},
        }
        case = MergeCase(tmp_path, rows)
        for site, values in rows.items():
            case.link(("1", site, f"p_{site}"))
            case.table("places", site, study_id=f"p_{site}", **values)
        first_site = ranked("take_first_site")
        rules = {
            "label": first_site,
            "region": first_site,
            "code": {"tether": "region"},
        }
        assert case.run({"places": by_person(rules)}) == 0, "merge failed"
        result = case.output("places").iloc[0]
        assert result["label"] == "from b", "The first site with a value wins"
        assert (result["region"], result["code"]) == ("from c", "c's code"), (
            "A tethered column comes from its lead's site, even when an "
            "earlier site has a value for it"
        )

    def test_order_of_strategies_matters(self, tmp_path: Path) -> None:
        """Listing the same strategies in reverse order picks the other winner."""
        rows = [("a", "1", "2024-03-01"), ("b", "0", "2024-06-01")]
        value_then_latest = list(reversed(self.LATEST_THEN_VALUE))
        winner = self._merge_status(tmp_path, rows, value_then_latest)
        assert winner == ("1", "2024-03-01")


@pytest.mark.reliability
class TestTetheredColumns:
    """Tethered columns pull values from the same winning row."""

    def test_tether_same_row_guarantee(self, tmp_path: Path) -> None:
        """All tethered columns come from the lead's winning row."""
        case = MergeCase(tmp_path).link(*ONE_PERSON)
        case.table(
            "geo",
            "site_a",
            study_id="p1",
            location_id="loc_100",
            zip5="02111",
            fips="25025",
            visit_date="2023-01-01",
        )
        case.table(
            "geo",
            "site_b",
            study_id="p2",
            location_id="loc_200",
            zip5="90210",
            fips="06037",
            visit_date="2024-03-15",
        )
        tethered = {"tether": "location_id"}
        rules = {
            "location_id": ranked("take_latest", column="visit_date"),
            "zip5": tethered,
            "fips": tethered,
            "visit_date": tethered,
        }
        assert case.run({"geo": by_person(rules)}) == 0
        result = case.output("geo")
        assert result["location_id"].iloc[0] == "loc_200"
        assert result["zip5"].iloc[0] == "90210"
        assert result["fips"].iloc[0] == "06037"
        assert result["visit_date"].iloc[0] == "2024-03-15"


# ---------------------------------------------------------------------------
# Partitioned tables
# ---------------------------------------------------------------------------


@pytest.mark.reliability
class TestPartitionedTables:
    """Partitioned tables apply different strategies per partition value."""

    def test_different_strategies_per_partition(self, tmp_path: Path) -> None:
        """Each partition resolves with its declared strategy."""
        case = MergeCase(tmp_path).link(*ONE_PERSON)
        case.table(
            "metrics",
            "site_a",
            study_id=["p1", "p1"],
            metric_type=["CVR", "PS"],
            status=["3", "7"],
            mdate=["2024-01-01", "2022-06-01"],
        )
        case.table(
            "metrics",
            "site_b",
            study_id=["p2", "p2"],
            metric_type=["CVR", "PS"],
            status=["5", "2"],
            mdate=["2023-06-01", "2024-12-01"],
        )

        def partition(strategy: str, column: str) -> dict:
            rules = {"status": ranked(strategy, column=column)}
            return {"column": rules | {"mdate": {"tether": "status"}}}

        rc = case.run(
            {
                "metrics": {
                    "primary_key": ["person_id", "metric_type"],
                    "partition_by": "metric_type",
                    "partitions": {
                        "CVR": partition("take_highest", "status"),
                        "PS": partition("take_latest", "mdate"),
                    },
                }
            }
        )
        assert rc == 0
        result = case.output("metrics")
        cvr = result[result["metric_type"] == "CVR"]
        ps = result[result["metric_type"] == "PS"]
        assert cvr["status"].iloc[0] == "5"
        assert ps["status"].iloc[0] == "2"
        assert ps["mdate"].iloc[0] == "2024-12-01"


# ---------------------------------------------------------------------------
# Missing values
# ---------------------------------------------------------------------------


@pytest.mark.reliability
class TestMissingValues:
    """Missing values are excluded from comparison."""

    def _highest(self, tmp_path: Path, score_a: str, score_b: str) -> str:
        """Merge the score from site_a's ``score_a`` and site_b's ``score_b``."""
        case = MergeCase(tmp_path).link(*ONE_PERSON)
        case.table("vals", "site_a", study_id="p1", score=score_a)
        case.table("vals", "site_b", study_id="p2", score=score_b)
        rules = {"score": ranked("take_highest", column="score")}
        assert case.run({"vals": by_person(rules)}) == 0
        return case.output("vals")["score"].iloc[0]

    def test_partial_missing_skips_empty(self, tmp_path: Path) -> None:
        """Rows missing the target column are excluded, so the present value wins."""
        assert self._highest(tmp_path, "", "42") == "42"

    def test_all_missing_produces_empty(self, tmp_path: Path) -> None:
        """When every site's value is missing, the output is an empty string."""
        assert self._highest(tmp_path, "", "") == ""

    LATEST: ClassVar = {
        "value": ranked("take_latest", column="date"),
        "date": {"tether": "value"},
    }

    def test_take_latest_keeps_values_without_dates(self, tmp_path: Path) -> None:
        """take_latest keeps a value whose date is missing.

        This works the same way for a person with one record or several. A
        dated record counts as later than an undated one. Among undated
        records, the first site in `sites` wins, as in any tie.
        """
        case = MergeCase(tmp_path).link(
            ("1", "site_a", "p1"),
            ("1", "site_b", "p2"),
            ("2", "site_a", "p3"),
            ("2", "site_b", "p4"),
        )
        case.table(
            "vals",
            "site_a",
            study_id=["p1", "p3", "p9"],
            value=["a_undated", "a_undated", "only_record"],
            date=["", "", ""],
        )
        case.table(
            "vals",
            "site_b",
            study_id=["p2", "p4"],
            value=["b_undated", "b_dated"],
            date=["", "2024-01-01"],
        )
        assert case.run({"vals": by_person(self.LATEST)}) == 0
        result = case.output("vals")
        assert dict(zip(result["value"], result["date"], strict=True)) == {
            "a_undated": "",
            "b_dated": "2024-01-01",
            "only_record": "",
        }, (
            "Undated values are kept: by site order when no record has a date, "
            "after any dated record, and alone for a person with one record"
        )


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def _one_site(tmp_path: Path, table: str, **columns: object) -> MergeCase:
    """Make a case at site_a only, with one person, p1.

    The person's row in ``table`` has the values ``columns``.
    """
    case = MergeCase(tmp_path, ["site_a"]).link(("1", "site_a", "p1"))
    return case.table(table, "site_a", study_id="p1", **columns)


def _said(capsys: pytest.CaptureFixture[str], message: Message) -> bool:
    """Tell whether merge's output shows ``message``."""
    captured = capsys.readouterr()
    return shows(message, captured.out + captured.err)


@pytest.mark.usability
class TestValidationErrors:
    """Configuration and data validation errors are caught."""

    def test_ordinal_scale_violation(self, tmp_path: Path) -> None:
        """Values outside the ordinal scale produce a validation error."""
        case = _one_site(tmp_path, "demo", status="INVALID")
        scale = ["Low", "Medium", "High"]
        rules = {"status": ranked("take_top_ordinal", column="status", scale=scale)}
        assert case.run({"demo": by_person(rules)}) == 1

    @pytest.mark.parametrize(
        ("rank_by", "message"),
        [
            ([{"take_latest": {"column": "visit_date"}}], msg.RANK_COLUMN_NOT_IN_DATA),
            ([{"take_top_ordinal": {"column": "status"}}], msg.STRATEGY_SETTINGS),
            (
                [{"take_highest": {"column": "status", "scale": [1, 2]}}],
                msg.STRATEGY_SETTINGS,
            ),
            (
                [{"take_top_ordinal": {"column": "status", "scale": []}}],
                msg.SCALE_MISSING,
            ),
            ([{"take_first_site": {"column": "status"}}], msg.STRATEGY_NO_SETTINGS),
            ([{"take_first_site": None}], msg.STRATEGY_NO_SETTINGS),
            ([], msg.RANK_BY_NOT_LIST),
            (["take_highest"], msg.RANK_BY_ITEM),
            ([{"take_highest": {"column": "status"}}], msg.VALUES_NOT_NUMBERS),
        ],
    )
    def test_rank_by_mistakes(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        rank_by: object,
        message: Message,
    ) -> None:
        """Each mistake in rank_by is reported, and nothing is merged."""
        case = _one_site(tmp_path, "demo", status="Low")
        rc = case.run({"demo": by_person({"status": {"rank_by": rank_by}})})
        assert rc == 1, "merge should refuse the config"
        assert _said(capsys, message), f"should say {message.text}"
        assert not (tmp_path / "output").exists(), "nothing should be written"

    @pytest.mark.parametrize(
        ("rules", "message"),
        [
            ({"status": 3}, msg.COLUMN_RULE_NOT_SETTINGS),
            (
                {"status": {"tether": "score"}, "score": {"tether": "status"}},
                msg.TETHER_CIRCULAR,
            ),
            (
                {"status": {"tether": "status"}, "score": {"drop": True}},
                msg.TETHER_CIRCULAR,
            ),
            ({"status": "tether"}, msg.COLUMN_RULE_NOT_SETTINGS),
            ({"status": {"tether": "score"}, "score": 3}, msg.COLUMN_RULE_NOT_SETTINGS),
            (
                {"status": {"tether": "score"}, "score": 3},
                msg.TETHER_TARGET_NO_STRATEGY,
            ),
            (
                {"status": {"tether": ["score"]}, "score": {"drop": True}},
                msg.TETHER_UNKNOWN,
            ),
        ],
    )
    def test_column_rule_mistakes(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        rules: dict,
        message: Message,
    ) -> None:
        """A column rule that is not a mapping is reported, never a crash.

        The same holds for a tether that is not a column's name.
        """
        case = _one_site(tmp_path, "demo", status="Low", score="1")
        assert case.run({"demo": by_person(rules)}) == 1, (
            "merge should refuse the config"
        )
        assert _said(capsys, message), f"should say {message.text}"

    @pytest.mark.parametrize(
        ("sites", "message"),
        [
            ([], msg.MERGE_SITES_NOT_LIST),
            (["site a"], msg.INVALID_SITE),
            (["site_a", "site_a"], msg.SITES_REPEATED),
        ],
    )
    def test_sites_mistakes(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        sites: list[str],
        message: Message,
    ) -> None:
        """Each mistake in `sites` is reported, and nothing is merged."""
        case = _one_site(tmp_path, "demo")
        rc = case.run({"demo": {"primary_key": ["person_id"]}}, sites=sites)
        assert rc == 1, "merge should refuse the sites"
        assert _said(capsys, message), f"should say {message.text}"
        assert not (tmp_path / "output").exists(), "nothing should be written"

    def test_undeclared_column(self, tmp_path: Path) -> None:
        """Columns present in data but not declared in config are an error."""
        case = _one_site(tmp_path, "data", known_col="x", surprise_col="y")
        rules = {"known_col": ranked("take_highest", column="known_col")}
        assert case.run({"data": by_person(rules)}) == 1

    def test_undeclared_partition_value(self, tmp_path: Path) -> None:
        """Partition values in data but not in config are an error."""
        case = MergeCase(tmp_path, ["site_a"]).link(("1", "site_a", "p1"))
        case.table(
            "health",
            "site_a",
            study_id=["p1", "p1"],
            feature=["CVR", "UNKNOWN_FEAT"],
            status=["5", "3"],
        )
        rules = {"status": ranked("take_highest", column="status")}
        rc = case.run(
            {
                "health": {
                    "primary_key": ["person_id", "feature"],
                    "partition_by": "feature",
                    "partitions": {"CVR": {"column": rules}},
                }
            }
        )
        assert rc == 1

    def test_missing_site_file(self, tmp_path: Path) -> None:
        """A missing site table is a hard error."""
        case = MergeCase(tmp_path, ["site_a"]).link(("1", "site_a", "p1"))
        assert case.run({"missing_table": {"primary_key": ["person_id"]}}) == 1

    def test_tether_to_nonexistent_column(self, tmp_path: Path) -> None:
        """A tether that refers to a nonexistent lead column is a validation error."""
        case = _one_site(tmp_path, "data", col_a="x")
        rules = {"col_a": {"tether": "nonexistent_lead"}}
        assert case.run({"data": by_person(rules)}) == 1


# ---------------------------------------------------------------------------
# Person ID reassignment
# ---------------------------------------------------------------------------


@pytest.mark.reliability
class TestPersonIdReassignment:
    """Output person_ids are sequential and carry no provenance."""

    def test_no_original_ids_leak(self, tmp_path: Path) -> None:
        """Original person_ids from site data do not appear in output."""
        case = MergeCase(tmp_path).link(
            ("1", "site_a", "orig_001"),
            ("1", "site_b", "orig_999"),
            ("2", "site_a", "orig_002"),
        )
        case.table("data", "site_a", study_id=["orig_001", "orig_002"], val=["1", "2"])
        case.table("data", "site_b", study_id="orig_999", val="3")
        rules = {"val": ranked("take_highest", column="val")}
        assert case.run({"data": by_person(rules)}) == 0
        result = case.output("data")
        original_ids = {"orig_001", "orig_999", "orig_002"}
        output_ids = set(result["person_id"])
        assert output_ids.isdisjoint(original_ids)
        assert all(pid.isdigit() for pid in output_ids)


# ---------------------------------------------------------------------------
# Compound primary key
# ---------------------------------------------------------------------------


@pytest.mark.reliability
class TestCompoundPrimaryKey:
    """Tables with multiple primary key columns merge correctly."""

    def test_groups_by_full_primary_key(self, tmp_path: Path) -> None:
        """Merge groups by all primary key columns, not just person_id."""
        case = MergeCase(tmp_path).link(*ONE_PERSON)
        years = ["2023", "2024"]
        case.table(
            "yearly", "site_a", study_id=["p1"] * 2, year=years, score=["10", "20"]
        )
        case.table(
            "yearly", "site_b", study_id=["p2"] * 2, year=years, score=["15", "18"]
        )
        rules = {"score": ranked("take_highest", column="score")}
        assert case.run({"yearly": by_person(rules, "year")}) == 0
        result = case.output("yearly")
        assert len(result) == 2
        row_2023 = result[result["year"] == "2023"]
        row_2024 = result[result["year"] == "2024"]
        assert row_2023["score"].iloc[0] == "15"
        assert row_2024["score"].iloc[0] == "20"


# ---------------------------------------------------------------------------
# Key-only table
# ---------------------------------------------------------------------------


@pytest.mark.reliability
class TestKeyOnlyTable:
    """A table with only primary_key and no column block produces unique keys."""

    def test_key_only_output(self, tmp_path: Path) -> None:
        """Key-only table produces one row per unique person."""
        case = MergeCase(tmp_path).link(*ONE_PERSON, ("2", "site_a", "p3"))
        case.table("ids", "site_a", study_id=["p1", "p3"])
        case.table("ids", "site_b", study_id="p2")
        assert case.run({"ids": by_person({})}) == 0
        result = case.output("ids")
        assert len(result) == 2
        assert set(result.columns) == {"person_id"}


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.reliability
class TestNewIds:
    """Each run gives every person a new id: a random shuffle of 1 to M."""

    def test_new_shuffle_each_run(self, tmp_path: Path) -> None:
        """Two runs merge the same rows, under newly shuffled ids.

        Each person gets an id from 1 to M, but the ids follow neither the
        linkage file nor the sites' study_ids.
        """
        n = 20
        case = MergeCase(tmp_path)
        study_ids = [str(i) for i in range(1, n + 1)]
        for site in SITES:
            case.link(*((i, site, i) for i in study_ids))
            case.table("data", site, study_id=study_ids, val=list(range(n)))
        tables = {"data": by_person({"val": ranked("take_highest", column="val")})}
        runs = []
        for output in ("run1", "run2"):
            assert case.run(tables, output=output) == 0
            runs.append(case.output("data", output))
        for result in runs:
            assert sorted(result["person_id"], key=int) == study_ids
            assert sorted(result["val"], key=int) == [str(i) for i in range(n)]
        ids = [dict(zip(r["val"], r["person_id"], strict=True)) for r in runs]
        assert ids[0] != ids[1], "Each run shuffles anew"
        assert ids[0] != {str(i): str(i + 1) for i in range(n)}, "Not in group order"


# ---------------------------------------------------------------------------
# Drop columns
# ---------------------------------------------------------------------------


@pytest.mark.reliability
class TestDropColumns:
    """Columns marked drop: true are excluded from output."""

    def test_drop_removes_from_output(self, tmp_path: Path) -> None:
        """A dropped column does not appear in the output CSV."""
        case = _one_site(tmp_path, "data", keep_col="7", drop_col="secret")
        rules = {
            "keep_col": ranked("take_highest", column="keep_col"),
            "drop_col": {"drop": True},
        }
        assert case.run({"data": by_person(rules)}) == 0
        result = case.output("data")
        assert "drop_col" not in result.columns
        assert "keep_col" in result.columns


# ---------------------------------------------------------------------------


@pytest.mark.reliability
class TestPeopleAcrossTables:
    """Each person is one person_id in every table, and only theirs."""

    def _case(self, tmp_path: Path) -> MergeCase:
        case = MergeCase(tmp_path).link(("1", "site_a", "1"), ("1", "site_b", "9"))
        case.table("person", "site_a", study_id=["1", "2", "3"], score=["5", "6", "7"])
        case.table("person", "site_b", study_id=["9", "2"], score=["8", "1"])
        case.table("visits", "site_a", study_id="3", visit="4")
        return case.table("visits", "site_b", study_id="9", visit="5")

    def test_unlinked_records_stay_apart(self, tmp_path: Path) -> None:
        """Unlinked records from different sites are different people.

        This holds even when they have the same id.
        """
        case = self._case(tmp_path)
        rules = {"score": ranked("take_highest", column="score")}
        assert case.run({"person": by_person(rules)}) == 0
        assert sorted(case.output("person")["score"]) == ["1", "6", "7", "8"], (
            "site_a's person 2 and site_b's person 2 are not linked, so both stay"
        )

    def test_person_ids_agree_across_tables(self, tmp_path: Path) -> None:
        """A person has the same person_id in every merged table."""
        case = self._case(tmp_path)
        tables = {
            "person": by_person({"score": ranked("take_highest", column="score")}),
            "visits": by_person({"visit": ranked("take_highest", column="visit")}),
        }
        assert case.run(tables) == 0
        person, visits = case.output("person"), case.output("visits")
        id_of_score = dict(zip(person["score"], person["person_id"], strict=True))
        id_of_visit = dict(zip(visits["visit"], visits["person_id"], strict=True))
        assert id_of_visit["4"] == id_of_score["7"], "site_a's person 3"
        assert id_of_visit["5"] == id_of_score["8"], "the linked person"

    def test_failure_writes_nothing(self, tmp_path: Path) -> None:
        """If any table cannot be merged, no output is written."""
        case = self._case(tmp_path)
        tables = {
            "person": by_person({"score": ranked("take_highest", column="score")}),
            "visits": by_person({"visit": ranked("take_lowest", column="visit")}),
        }
        assert case.run(tables) == 1
        assert not (tmp_path / "output").exists()


@pytest.mark.reliability
class TestRuns:
    """Merge joins a site's tables to the linkage only if both come from one clean run.

    The tables and the hashes must come from the same run of pprl clean at
    that site, so that they share study_ids.
    """

    def _files(self, tmp_path: Path, clean_of_tables: dict | None) -> None:
        """Make a linkage file of site_a's and site_b's hashes, and their tables.

        Each hash file is made from its site's cleaned file. Each site's tables
        are packed from the cleaned file that ``clean_of_tables`` gives, which
        is the site's own by default.
        """
        cleaned, hashes = {}, []
        for site in SITES:
            cleaned[site] = _pprl_file.read(
                tmp_path / file_name("clean", site)
                if (tmp_path / file_name("clean", site)).exists()
                else self._clean(tmp_path, site, file_name("clean", site))
            )
            hashes.append(
                _pprl_file.read(
                    self._write(
                        tmp_path / file_name("hashes", site),
                        "hashes",
                        {"hashes": pd.DataFrame({"study_id": ["1"], "hash": ["AA=="]})},
                        provenance=provenance(site),
                        inputs=[cleaned[site]],
                    )
                )
            )
        self._write(
            tmp_path / file_name("linkages"),
            "linkages",
            {
                "linkages": pd.DataFrame(
                    {
                        "group_id": ["1", "1"],
                        "site": list(SITES),
                        "study_id": ["1", "1"],
                    }
                )
            },
            provenance=provenance(),
            inputs=hashes,
        )
        for site in SITES:
            source = (clean_of_tables or {}).get(site, cleaned[site])
            self._write(
                tmp_path / file_name("tables", site),
                "tables",
                {"scores": pd.DataFrame({"study_id": ["1"], "score": ["5"]})},
                provenance=provenance(site),
                inputs=[source],
            )

    @staticmethod
    def _write(
        path: Path,
        kind: str,
        members: dict,
        *,
        provenance: dict,
        inputs: Iterable[PprlFile | dict] = (),
    ) -> Path:
        _pprl_file.write(path, kind, members, provenance=provenance, inputs=inputs)
        return path

    def _clean(self, tmp_path: Path, site: str, name: str) -> Path:
        rows = pd.DataFrame({"study_id": ["1"], "name": ["x"]})
        return self._write(
            tmp_path / name,
            "clean",
            {"patients": rows},
            provenance=provenance(site),
        )

    def _merge(self, tmp_path: Path) -> int:
        """Merge the files that ``_files`` wrote, which the case keeps."""
        rules = {"score": ranked("take_highest", column="score")}
        return MergeCase(tmp_path).run({"scores": by_person(rules)})

    def test_same_run(self, tmp_path: Path) -> None:
        """Tables packed from the cleaned file the hashes came from are merged."""
        self._files(tmp_path, {})
        assert self._merge(tmp_path) == 0

    def test_other_run(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Tables packed after a different run of clean are refused.

        Their study_ids don't match the linked hashes' study_ids.
        """
        rerun = _pprl_file.read(self._clean(tmp_path, "site_a", "rerun.clean.pprl"))
        self._files(tmp_path, {"site_a": rerun})
        assert self._merge(tmp_path) == 1
        output = capsys.readouterr().err
        assert shows(msg.FILE_OTHER_RUN, output), output
        [failed] = [
            line for line in strip_ansi(output).splitlines() if "[FAIL]" in line
        ]
        assert "site_a tables" in failed, failed
        assert not (tmp_path / "output").exists()


# ---------------------------------------------------------------------------
# The merge of one table, compared with the reference: pprl's earlier merge,
# which worked one group at a time
# ---------------------------------------------------------------------------

_SCALE = ["low", "medium", "high"]


def _random_rank_by(rng: np.random.Generator) -> list:
    """Draw one to three strategies, each comparing a column of the random table."""
    choices = [
        {"take_highest": {"column": "score"}},
        {"take_latest": {"column": "date"}},
        {"take_top_ordinal": {"column": "level", "scale": _SCALE}},
        {"take_first_site": {}},
    ]
    picked = rng.choice(len(choices), size=rng.integers(1, 4), replace=False)
    return [choices[i] for i in picked]


def _random_rules(rng: np.random.Generator) -> dict:
    """Draw rules for score, date, level and note.

    Two of the columns are ranked, note is tethered to one of them, and
    level is sometimes dropped.
    """
    rules = {
        "score": {"rank_by": _random_rank_by(rng)},
        "date": {"rank_by": _random_rank_by(rng)},
        "level": {"tether": "date"},
        "note": {"tether": "score" if rng.random() < 0.5 else "date"},
    }
    if rng.random() < 0.3:
        rules["level"] = {"drop": True}
    return rules


def _random_table(rng: np.random.Generator, sites: list[str]) -> pd.DataFrame:
    """Draw rows of a table at ``sites``.

    There are few people and visits, so many rows share a primary key, with
    ties and empty values.
    """
    n = int(rng.integers(20, 300))

    def sometimes_empty(values: Iterable[str]) -> list[str]:
        return [v if rng.random() > 0.2 else "" for v in values]

    dates = [
        f"2024-0{rng.integers(1, 4)}-0{rng.integers(1, 4)}"
        + (" 10:00:00" if rng.random() < 0.2 else "")
        for _ in range(n)
    ]
    return pd.DataFrame(
        {
            GROUP_COLUMN: [f"p{i}" for i in rng.integers(0, 15, n)],
            "visit": [f"v{i}" for i in rng.integers(0, 3, n)],
            "kind": rng.choice(["a", "b", "c"], n),
            "score": sometimes_empty([str(v) for v in rng.integers(0, 4, n)]),
            "date": sometimes_empty(dates),
            "level": sometimes_empty(rng.choice(_SCALE, n)),
            "note": sometimes_empty([f"n{i}" for i in rng.integers(0, 3, n)]),
            SITE_COLUMN: rng.choice(sites, n),
        }
    )


@pytest.mark.reliability
class TestAgainstReference:
    """Merge compared with the earlier merge, which worked one group at a time."""

    @pytest.mark.parametrize("seed", seeds(50, quick=5))
    def test_same_rows(self, seed: int) -> None:
        """On random tables and rules, merge keeps the same values as the earlier merge.

        For simple or partitioned tables, every row has the same value as the
        earlier merge produced, one group at a time.
        """
        rng = np.random.default_rng(seed)
        sites = ["site_a", "site_b", "site_c"][: int(rng.integers(2, 4))]
        order = list(rng.permutation(sites))
        table = _random_table(rng, sites)
        key = ["person_id", "visit"]
        if seed % 2:  # A simple table, without the partition column
            table = table.drop(columns="kind")
            config = {"primary_key": key, "column": _random_rules(rng)}
        else:
            config = {
                "primary_key": [*key, "kind"],
                "partition_by": "kind",
                "partitions": {k: {"column": _random_rules(rng)} for k in "abc"},
            }
        merged, _ = merge_table(table, config, order, "person_id")
        expected = merge_reference.merge_table(table, config, order, "person_id")
        pd.testing.assert_frame_equal(  # Compare text: pandas may infer other types
            merged.reset_index(drop=True),
            expected.reset_index(drop=True),
            check_dtype=False,
        )


# ---------------------------------------------------------------------------
# What merge reports it decided, and its checks of its own counts
# ---------------------------------------------------------------------------


def _rows(*rows: tuple[str, ...]) -> pd.DataFrame:
    """Make a table's rows from every site, each (person, site, score, date, note)."""
    columns = [GROUP_COLUMN, SITE_COLUMN, "score", "date", "note"]
    return pd.DataFrame([dict(zip(columns, row, strict=True)) for row in rows])


_RULES = {
    "primary_key": ["person_id"],
    "column": {
        "score": {
            "rank_by": [
                {"take_latest": {"column": "date"}},
                {"take_highest": {"column": "score"}},
            ]
        },
        "date": {"tether": "score"},
        "note": {"tether": "score"},
    },
}


@pytest.mark.reliability
class TestMergeReport:
    """What merge reports about each table."""

    def test_counts(self) -> None:
        """Each column's counts match what merge did.

        The counts cover merged rows where the sites differed and what decided
        each, site values not kept, and merged rows with no value.
        """
        df = _rows(
            ("1", "site_a", "5", "2024-01-01", "x"),  # Older date: not kept
            ("1", "site_b", "7", "2024-02-01", "x"),
            ("2", "site_a", "3", "2024-01-01", "y"),  # Same date: score decides
            ("2", "site_b", "4", "2024-01-01", ""),
            ("3", "site_a", "6", "", "z"),  # No dates to compare: site order decides
            ("3", "site_b", "8", "", "z"),
            ("4", "site_a", "2", "2024-03-01", "w"),  # The sites agree
            ("4", "site_b", "2", "2024-03-01", "w"),
            ("5", "site_b", "", "", ""),  # No value at all
        )
        _, report = merge_table(df, _RULES, ["site_a", "site_b"], "person_id")
        [part] = report.values()
        assert (part.rows_in, part.rows_out) == (9, 5)
        score, date, note = (part.columns[c] for c in ("score", "date", "note"))
        assert (score.conflicts, score.changed, score.empty) == (3, 3, 1)
        assert score.decided == {
            "take_latest on date": 1,
            "take_highest on score": 2,
        }, "score 6 and 8: no date, so take_highest decides"
        assert score.lead is None
        assert note.lead == "score"
        assert (date.conflicts, date.changed, date.empty) == (1, 1, 2)
        assert (note.conflicts, note.changed, note.empty) == (0, 1, 2), (
            "person 2's note comes from site_b's row, which has none: one value "
            "and an empty one do not differ, but the value is not kept"
        )

    def test_decided_by_sites(self) -> None:
        """A tie under every strategy is decided by the order of the sites.

        This applies when the values differ, and it is counted that way.
        """
        df = _rows(
            ("1", "site_a", "5", "2024-01-01", "x"),
            ("1", "site_b", "5", "2024-01-01", "y"),
        )
        merged, report = merge_table(df, _RULES, ["site_b", "site_a"], "person_id")
        assert merged["note"].tolist() == ["y"], "site_b is first"
        note = report[None].columns["note"]
        assert (note.conflicts, note.changed) == (1, 1)
        assert report[None].columns["score"].conflicts == 0, "the scores agree"

    @pytest.mark.parametrize("seed", seeds(20, quick=3))
    def test_site_order_decides_only_its_rows(self, seed: int) -> None:
        """Reversing the sites changes only the rows that their order decided.

        A ranked column's value changes only in rows counted as decided by the
        order of the sites. The exception is a column ranked by
        take_first_site, which also compares the sites.
        """
        rng = np.random.default_rng(seed)
        table = _random_table(rng, ["site_a", "site_b"]).drop(columns="kind")
        config = {"primary_key": ["person_id", "visit"], "column": _random_rules(rng)}
        first, report = merge_table(table, config, ["site_a", "site_b"], "person_id")
        second, _ = merge_table(table, config, ["site_b", "site_a"], "person_id")
        for column, counts in report[None].columns.items():
            rank_by = config["column"][column].get("rank_by", [])
            if counts.lead or any("take_first_site" in item for item in rank_by):
                continue
            by_sites = counts.decided.get(msg.MERGE_BY_SITES, 0)
            differ = int((first[column] != second[column]).sum())
            assert differ <= by_sites, (column, differ, by_sites)

    def test_tethered_without_lead(self) -> None:
        """A tethered column whose lead is empty keeps no value.

        If the lead column is empty in a row, the tethered value in that row is
        not kept, because no row wins. The merged row has no value, which
        merge's checks allow.
        """
        df = _rows(("1", "site_a", "", "", "x"))  # A note, but no score
        merged, report = merge_table(df, _RULES, ["site_a", "site_b"], "person_id")
        note = report[None].columns["note"]
        assert merged["note"].tolist() == [""]
        assert (note.changed, note.empty) == (1, 1)
        assert inconsistency(merged, ["person_id"], report) is None

    @pytest.mark.parametrize(
        ("check", "counts"),
        [
            ("rows", {"rows_out": 10}),
            ("conflicts", {"conflicts": 4}),
            ("changed", {"changed": 3}),
            ("changed", {"changed": 4, "empty": 1, "lead": "date"}),
            ("empty", {"empty": 4}),
            ("decided", {"decided": {"x": 1}}),
        ],
    )
    def test_checks(self, check: str, counts: dict) -> None:
        """Merge's checks of its own counts catch each impossible count."""
        rows = {k: counts.pop(k) for k in ("rows_out",) if k in counts}
        part = PartCounts(rows_in=5, rows_out=rows.get("rows_out", 3), columns={})
        part.columns["score"] = ColumnCounts(counts.pop("lead", None), **counts)
        result = pd.DataFrame({"person_id": ["1", "2", "3"]})
        found = inconsistency(result, ["person_id"], {None: part})
        assert found == msg.MERGE_CHECKS[check](column="score")
        repeated = pd.DataFrame({"person_id": ["1", "1"]})
        assert inconsistency(repeated, ["person_id"], {}) == msg.MERGE_CHECKS["key"]()

    def test_failed_check_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed check stops merge before it writes anything.

        It also asks the user to send the log.
        """
        case = MergeCase(tmp_path).link(*ONE_PERSON)
        case.table("scores", "site_a", study_id="p1", score="5")
        case.table("scores", "site_b", study_id="p2", score="9")
        monkeypatch.setattr(_merge, "inconsistency", lambda *_: "a test's fault")
        rc = case.run(
            {"scores": by_person({"score": ranked("take_highest", column="score")})}
        )
        assert rc == 1
        assert not (tmp_path / "output").exists()

    def test_shown(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Merge shows the order of the sites and each table's counts.

        For each table, it shows the rows in and out, and each column's counts
        across its partitions.
        """
        case = MergeCase(tmp_path).link(*ONE_PERSON)
        for site, study_id, value in (("site_a", "p1", "5"), ("site_b", "p2", "9")):
            case.table(
                "labs",
                site,
                study_id=[study_id] * 2,
                test=["a", "b"],
                value=[value] * 2,
            )
        rule = {"column": {"value": ranked("take_highest", column="value")}}
        rc = case.run(
            {
                "labs": {
                    "primary_key": ["person_id", "test"],
                    "partition_by": "test",
                    "partitions": {"a": rule, "b": rule},
                }
            }
        )
        output = strip_ansi(capsys.readouterr().out)
        assert rc == 0, output
        assert shows(msg.MERGE_SITE_ORDER(sites="site_a, site_b"), output), output
        assert shows(
            msg.MERGE_TABLE_REPORT(table="labs", rows_in=4, rows_out=2), output
        )
        assert "value: 2 merged rows where the sites differed (2 by take_highest" in (
            output
        ), output


# ---------------------------------------------------------------------------
# Merging in buckets of people, against merging in one
# ---------------------------------------------------------------------------


def _at_sites(case: MergeCase, table: pd.DataFrame) -> MergeCase:
    """Give ``case`` each site's rows of ``table``, and the linkages.

    ``table`` is a random table with rows from every site, written as pack
    writes them (each person has the same study_id at every site). The
    linkages cover every person with records at more than one site.
    """
    for site in case.sites:
        rows = table[table[SITE_COLUMN] == site]
        renamed = rows.drop(columns=SITE_COLUMN).rename(
            columns={GROUP_COLUMN: "study_id"}
        )
        case.table("visits", site, renamed)
    at_sites = table.groupby(GROUP_COLUMN)[SITE_COLUMN].unique()
    return case.link(
        *(
            (str(person), site, str(person))
            for person, found in at_sites.items()
            if len(found) > 1
            for site in found
        )
    )


@pytest.mark.reliability
class TestBuckets:
    """Merging in buckets of people."""

    @pytest.mark.parametrize("seed", seeds(10, quick=2))
    def test_same_as_one_bucket(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed: int
    ) -> None:
        """Merging in buckets of people gives the same table as merging in one bucket.

        With a few rows per bucket, the result matches row for row, in the
        same order, with the same report.
        """
        rng = np.random.default_rng(seed)
        sites = ["site_a", "site_b"]
        table = _random_table(rng, sites)
        key = ["person_id", "visit"]
        if seed % 2:  # A simple table, without the partition column
            table = table.drop(columns="kind")
            config = {"primary_key": key, "column": _random_rules(rng)}
        else:
            config = {
                "primary_key": [*key, "kind"],
                "partition_by": "kind",
                "partitions": {k: {"column": _random_rules(rng)} for k in "abc"},
            }
        case = _at_sites(MergeCase(tmp_path, sites), table)
        merged = {}
        for buckets, rows in (("one", 10**9), ("many", 7)):
            monkeypatch.setattr(_merge, "BUCKET_ROWS", rows)
            monkeypatch.setattr(
                _merge.secrets, "SystemRandom", lambda: random.Random(seed)
            )
            assert case.run({"visits": config}, output=buckets) == 0
            merged[buckets] = read_text(tmp_path / buckets / "visits.csv")
            assert not list(tmp_path.glob(".tmp.*")), "No working file is left"
        assert merged["one"] == merged["many"]
        assert len(case.output("visits", "many")) > 0
        assert len(table) > 7, "Many buckets"
