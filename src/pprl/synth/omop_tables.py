"""Build every OMOP CDM table for one site.

The scenario states most values. This module adds what the scenario leaves
out: ids, visit and observation period rows, person records, the concept
hierarchy, and defaults for required columns. The synth guide lists each
addition under "What synth fills in". Tables with no rows are still
written, empty, with their full header.
"""

from datetime import date, datetime, time

from .omop_cdm import (
    OMOP_TABLES,
    OmopRow,
    OmopTable,
    is_concept_column,
)
from .scenario import ADDRESS_KEYS, Scenario, plan_visits
from .site_records import person_row

EHR_TYPE_CONCEPT = 32817
UNMAPPED_CONCEPT = 0
OMOP_CDM_5_4_CONCEPT = 756265


class SiteTables:
    """Build one site's tables. Ids of person-level rows restart at 1 at each site."""

    def __init__(self, scenario: Scenario, records: list, site: str) -> None:
        self.scenario = scenario
        self.records = records
        self.site = site
        self.last_ids = {}
        self.visits = []
        self.visit_ids = {}  # (person_id, date) -> visit_occurrence_id
        self.person_locations = {}  # person_id -> location row
        self._number_visits()
        if scenario.person_locations:
            self._add_person_locations()

    def next_id(self, table_name: str) -> int:
        """Return the next row id of ``table_name``, counting it."""
        self.last_ids[table_name] = self.last_ids.get(table_name, 0) + 1
        return self.last_ids[table_name]

    def tables(self) -> list:
        """Return ``(table, rows)`` pairs for every CDM table, in CDM order."""
        return [
            (table, [fill_unset_columns(table, row) for row in self.rows(table)])
            for table in OMOP_TABLES
        ]

    def rows(self, table: OmopTable) -> list:
        """List the rows of ``table`` that the site writes."""
        made = {
            "person": self.person_rows,
            "observation_period": self.observation_period_rows,
            "visit_occurrence": lambda: self.visits,
            "concept": lambda: concept_rows(self.scenario, self.site),
            "concept_ancestor": lambda: concept_ancestor_rows(self.scenario, self.site),
            "cdm_source": lambda: [cdm_source_row(self.scenario, self.site)],
        }.get(table.name)
        if made is not None:
            return made()
        if table.is_person_level:
            return self.event_rows(table)
        rows = [
            dict(row) for row in self.scenario.shared_table_rows(table.name, self.site)
        ]
        if table.name == "location":
            rows.extend(self.person_locations.values())
        return rows

    # ---- Person-level tables -------------------------------------------------

    def _number_visits(self) -> None:
        """Create one visit, with an id, per date of the site's visit-linked events.

        A visit at one of the scenario's care sites that this site doesn't
        have took place elsewhere, so the site records it without a care site.
        """
        elsewhere = {
            row["care_site_id"] for row in self.scenario.shared_table_rows("care_site")
        } - {
            row["care_site_id"]
            for row in self.scenario.shared_table_rows("care_site", self.site)
        }
        for record in self.records:
            for visit_date, planned_visit in plan_visits(record.events):
                visit = dict(planned_visit)
                if visit.get("care_site_id") in elsewhere:
                    del visit["care_site_id"]
                visit["visit_occurrence_id"] = self.next_id("visit_occurrence")
                visit["person_id"] = record.person_id
                self.visit_ids[(record.person_id, visit_date)] = visit[
                    "visit_occurrence_id"
                ]
                self.visits.append(visit)

    def _add_person_locations(self) -> None:
        """Add a location row for each person's address as this site records it.

        The rows are numbered after the scenario's own locations, in
        person_id order. A person gets no row if the scenario states their
        location, or if their address is empty at this site.
        """
        last_id = max(
            (row["location_id"] for row in self.scenario.shared_table_rows("location")),
            default=0,
        )
        for record in self.records:
            stated = {
                **record.individual.person_columns,
                **record.differences.person_columns,
            }
            address = {key: record.pii.get(key) for key in ADDRESS_KEYS}
            if "location_id" in stated or not any(address.values()):
                continue
            last_id += 1
            self.person_locations[record.person_id] = {
                "location_id": last_id,
                **address,
            }

    def person_rows(self) -> list:
        """Return each individual's person record at this site (see ``person_row``)."""
        rows = []
        for record in self.records:
            row = person_row(
                self.scenario,
                record.individual,
                record.birth_date,
                record.attributes,
                record.differences.person_columns,
            )
            row["person_id"] = record.person_id
            if record.person_id in self.person_locations:
                location = self.person_locations[record.person_id]
                row["location_id"] = location["location_id"]
            rows.append(row)
        return rows

    def observation_period_rows(self) -> list:
        """Return one observation period per individual.

        The period runs from the earliest to the latest date on any of their
        events at this site, counting both start and end dates but not visit
        dates. An individual with no events gets a one-day period on their
        date of birth.
        """
        rows = []
        for record in self.records:
            dates = event_dates(record.events) or [record.birth_date]
            rows.append(
                {
                    "observation_period_id": self.next_id("observation_period"),
                    "person_id": record.person_id,
                    "observation_period_start_date": min(dates),
                    "observation_period_end_date": max(dates),
                }
            )
        return rows

    def event_rows(self, table: OmopTable) -> list:
        """Return the rows of an event table, each linked to its person and visit."""
        key = table.primary_key
        rows = []
        for record in self.records:
            for event in record.events:
                if event.table != table.name:
                    continue
                row = dict(event.columns)
                if key is not None and key not in row:
                    row[key] = self.next_id(table.name)
                row["person_id"] = record.person_id
                if table.links_to_visit:
                    row["visit_occurrence_id"] = self.visit_ids[
                        (record.person_id, event.start_date)
                    ]
                rows.append(row)
        return rows


def event_dates(events: list) -> list:
    """List every date among the columns of ``events``."""
    return [
        value
        for event in events
        for value in event.columns.values()
        if type(value) is date
    ]


# ---- Vocabulary and metadata tables ------------------------------------------


def concept_rows(scenario: Scenario, site: str | None = None) -> list:
    """List the scenario's concepts in concept_id order, with placeholder details."""
    rows = [dict(row) for row in scenario.shared_table_rows("concept", site)]
    for row in rows:
        row.setdefault("domain_id", "Unknown")
        row.setdefault("vocabulary_id", "Unknown")
        row.setdefault("concept_class_id", "Unknown")
        row.setdefault("standard_concept", "S")
        row.setdefault("concept_code", str(row["concept_id"]))
        row.setdefault("valid_start_date", date(1970, 1, 1))
        row.setdefault("valid_end_date", date(2099, 12, 31))
    return sorted(rows, key=lambda row: row["concept_id"])


def concept_ancestor_rows(scenario: Scenario, site: str | None = None) -> list:
    """Build the full concept hierarchy implied by the scenario's direct pairs.

    Every concept is its own ancestor at level 0. Beyond that, each concept
    is an ancestor of everything reachable through the ``concept_ancestor``
    pairs, with the shortest and longest path between them as the min and
    max levels of separation.
    """
    children = {}
    concept_ids = {
        row["concept_id"] for row in scenario.shared_table_rows("concept", site)
    }
    for pair in scenario.shared_table_rows("concept_ancestor"):
        ancestor, descendant = (
            pair["ancestor_concept_id"],
            pair["descendant_concept_id"],
        )
        concept_ids.update((ancestor, descendant))
        children.setdefault(ancestor, []).append(descendant)
    sorted_ids = sorted(concept_ids)
    rows = [_concept_ancestor_row(id_, id_, 0, 0) for id_ in sorted_ids]
    for ancestor in sorted_ids:
        levels = levels_of_separation(children, ancestor)
        rows.extend(
            _concept_ancestor_row(ancestor, descendant, *levels[descendant])
            for descendant in sorted(levels)
        )
    return rows


def _concept_ancestor_row(
    ancestor: int, descendant: int, min_levels: int, max_levels: int
) -> OmopRow:
    return {
        "ancestor_concept_id": ancestor,
        "descendant_concept_id": descendant,
        "min_levels_of_separation": min_levels,
        "max_levels_of_separation": max_levels,
    }


def levels_of_separation(children: dict, ancestor: int) -> dict:
    """Find the (shortest, longest) path from ``ancestor`` to each descendant.

    Every path that doesn't revisit a concept is followed.

    >>> levels_of_separation({1: [2, 3], 2: [3]}, 1)
    {2: (1, 1), 3: (1, 2)}
    """
    levels = {}

    def follow(node: int, depth: int, visited: set[int]) -> None:
        for child in children.get(node, []):
            if child in visited:
                continue
            shortest, longest = levels.get(child, (depth + 1, depth + 1))
            levels[child] = (min(shortest, depth + 1), max(longest, depth + 1))
            follow(child, depth + 1, visited | {child})

    follow(ancestor, 0, {ancestor})
    return levels


def cdm_source_row(scenario: Scenario, site: str | None = None) -> OmopRow:
    """Return the cdm_source row. Its release dates are today's date.

    The release dates are the only output that the scenario doesn't fix.
    """
    given = scenario.shared_table_rows("cdm_source", site)
    row = dict(given[0]) if given else {}
    row.setdefault("cdm_source_name", "Synthetic")
    row.setdefault("cdm_source_abbreviation", "SYN")
    today = date.today()  # noqa: DTZ011 (a release date, by the computer's calendar)
    row.setdefault("source_release_date", today)
    row.setdefault("cdm_release_date", today)
    row.setdefault("cdm_version", "5.4")
    row.setdefault("cdm_version_concept_id", OMOP_CDM_5_4_CONCEPT)
    return row


# ---- Defaults for columns the scenario leaves unset --------------------------


def fill_unset_columns(table: OmopTable, row: OmopRow) -> OmopRow:
    """Fill in the columns that the scenario leaves unset.

    - A ``*_datetime`` is midnight on its ``*_date``.
    - A required date is the row's start date (e.g. ``drug_exposure_end_date``).
    - A required ``*_type_concept_id`` is EHR (32817).
    - Any other required concept is unmapped (0).
    """
    start_date = table.start_date_column
    for column in table.columns:
        if column.name in row:
            continue
        if column.type is datetime:
            matching_date = row.get(column.name.replace("datetime", "date"))
            if type(matching_date) is date:
                row[column.name] = datetime.combine(matching_date, time())
        elif column.required and column.type is date and start_date in row:
            row[column.name] = row[start_date]
        elif column.required and column.name.endswith("_type_concept_id"):
            row[column.name] = EHR_TYPE_CONCEPT
        elif column.required and is_concept_column(column.name):
            row[column.name] = UNMAPPED_CONCEPT
    return row
