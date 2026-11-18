"""Define what a scenario contains, and read it from YAML. All parsing happens here.

A scenario is the simulated world that one YAML config describes: the
sites, the individuals who live in it, and what each site records about
them. Each top-level key is either one of the synth keys below or a shared
OMOP table (``omop_cdm`` explains how tables and columns are named). Any
other key is an error.
"""

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from pprl import _messages as msg
from pprl import _wording
from pprl._io import validated_site
from pprl._shared import RESERVED_KEYS

from .clinical_modules import MODULE_CONCEPT_ANCESTORS, MODULE_CONCEPTS, MODULES
from .omop_cdm import (
    PERSON_TABLE,
    VISIT_TABLE,
    OmopRow,
    OmopTable,
    known_table,
    omop_table,
    table_for_yaml_name,
    table_with_cdm_name,
)
from .seeded_random import SCRAMBLED_ID_LIMIT
from .yaml_reading import (
    RowNames,
    ScenarioError,
    ScenarioYaml,
    as_list,
    as_mapping,
    check_keys,
    check_omop_value,
    foreach_entry,
    map_items,
    map_list_items,
    read_rate,
    read_section,
    read_value,
    read_yaml,
    required_value,
    scenario_problem,
    set_omop_value,
    set_yaml_column,
    unknown_key_error,
    within,
    yaml_column,
    yaml_to,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

SCENARIO_KEYS = (
    "seed",
    "scale",
    "sites",
    "outputs",
    "individuals",
    "generated_individuals",
    "person_attributes",
    "pii",
    "relatives",
    "person_ids",
    "person_locations",
    "birth_years",
    "measurement_years",
    "event_sharing",
    "same_month",
    "site_coverage",
)

# `shared`: an individual's person_id is the same at every site.
# `per_site`: each site scrambles it into its own person_id.
PERSON_ID_MODES = ("shared", "per_site")

# How many of the sites that can record a generated event do record it.
EVENT_SHARING_KEYS = ("one_site", "several_sites", "all_sites")
# The kinds of relative a generated individual can be of an earlier one.
RELATIVE_KEYS = ("twin", "family", "junior")

# `omop` writes every CDM table; `patients` writes the pprl patient file.
OUTPUTS = ("omop", "patients")

# An individual's identifying information, as written in the patient file
# (where `name` is "first_name last_name"). These are synth keys, not OMOP
# columns.
PII_KEYS = ("first_name", "last_name", "city", "state", "zip")
ADDRESS_KEYS = ("city", "state", "zip")

INDIVIDUAL_KEYS = ("description", "expected", "sites", "at_sites", *PII_KEYS)

# Person columns that identify an individual or give their date of birth,
# so they cannot vary between sites.
FIXED_PERSON_COLUMNS = (
    "person_id",
    "person_source_value",
    "year_of_birth",
    "month_of_birth",
    "day_of_birth",
    "birth_datetime",
)

# Event columns set from the individual and the visit the event belongs to.
LINKING_EVENT_COLUMNS = ("person_id", "visit_occurrence_id")

# Concept 9202, Outpatient Visit. This is the visit concept used when no event
# on that date gives one.
DEFAULT_VISIT_CONCEPT = 9202


# ---- What a scenario contains ------------------------------------------------


@dataclass
class ClinicalEvent:
    """One row of a person-level table, such as a condition or a measurement."""

    table: str
    columns: OmopRow
    # Columns of the visit the event belongs to (tables linked to visits).
    visit: OmopRow = field(default_factory=dict)
    # The sites that record the event, or None for every site of the individual.
    sites: list | None = None

    @property
    def start_date(self) -> date:
        """Return the event's start date, from its table's start date column."""
        return self.columns[omop_table(self.table).start_date_column]


@dataclass
class SiteDifferences:
    """What one site records differently, exactly as stated under ``at_sites``."""

    person_columns: OmopRow = field(default_factory=dict)
    pii: dict = field(default_factory=dict)


@dataclass
class Individual:
    """One simulated person, as they really are.

    Each of the individual's ``sites`` keeps its own record of them, which
    may differ from the truth. ``individual_id`` identifies them in the
    scenario. Each site knows them by a person_id.
    """

    individual_id: int
    person_source_value: str
    sites: list
    description: str = ""
    expected: dict = field(default_factory=dict)
    person_columns: OmopRow = field(default_factory=dict)
    # PII stated in the YAML (first_name, city, ...), used as written.
    pii: dict = field(default_factory=dict)
    at_sites: dict = field(default_factory=dict)  # site -> SiteDifferences
    events: list = field(default_factory=list)
    # The clinical modules that draw a generated individual's events.
    modules: list = field(default_factory=list)  # [ModuleUse]


@dataclass
class ModuleUse:
    """A clinical module, and the chance that each individual in a group has it."""

    name: str
    prevalence: float = 1.0


@dataclass
class GeneratedGroup:
    """A number of generated individuals who all have records at the same sites."""

    sites: list
    count: int
    modules: list = field(default_factory=list)  # [ModuleUse]


@dataclass
class AttributePool:
    """A person column drawn from a weighted pool, unless an individual sets it."""

    column: str
    change_rate: float
    values: list
    weights: list


@dataclass
class Typos:
    """How often a site's copy of a field has typos, and how many at most."""

    rate: float = 0.0
    max_edits: int = 1


@dataclass
class TextVariation:
    """How a site's copy of one text field can differ from the truth."""

    typos: Typos = field(default_factory=Typos)
    case_change: float = 0.0
    missing: float = 0.0
    transliteration: float = 0.0


@dataclass
class PiiVariation:
    """How sites' copies of names and addresses differ from the truth."""

    name: TextVariation = field(default_factory=TextVariation)
    address_change_rate: float = 0.0
    address: TextVariation = field(default_factory=TextVariation)
    shuffle_rows: bool = False


@dataclass
class EventSharing:
    """How sites record generated events."""

    # Chances that an event is recorded at one, several or all of the sites
    # that can record it.
    rates: list = field(default_factory=lambda: [0.0, 0.0, 1.0])
    # Chance that a site records a redrawn copy in the same month.
    same_month: float = 0.0
    # Maps a site to the modules it records. A site not listed records every
    # module.
    coverage: dict = field(default_factory=dict)

    def covers(self, site: str, module: str) -> bool:
        """Tell whether ``site`` records the events of ``module``."""
        return site not in self.coverage or module in self.coverage[site]


@dataclass
class Scenario:
    """A scenario, read and checked. It holds everything synth needs to write data."""

    name: str
    seed: int | str
    sites: list
    outputs: list
    individuals: list
    row_names: RowNames
    # Rows of the shared tables (concept, location, care_site, ...) as given.
    shared_rows: dict
    attribute_pools: list
    pii_variation: PiiVariation
    # Maps table name to {position in shared_rows: the sites that write the
    # row}, for rows that list their `sites`. Any other row is written by every
    # site.
    shared_row_sites: dict = field(default_factory=dict)
    person_ids: str = "shared"
    # Whether each site writes a location row for each person's address.
    person_locations: bool = False
    birth_years: tuple | None = None  # (first, last), or None for the default
    measurement_years: tuple | None = None  # (first, last)
    event_sharing: EventSharing = field(default_factory=EventSharing)
    relatives: dict = field(default_factory=dict)  # kind -> rate
    generated: list = field(default_factory=list)  # The generated individuals

    def shared_table_rows(self, table_name: str, site: str | None = None) -> list:
        """Return a shared table's rows: all of them, or those that ``site`` writes."""
        rows = self.shared_rows.get(table_name, [])
        if site is None:
            return rows
        row_sites = self.shared_row_sites.get(table_name, {})
        return [
            row
            for position, row in enumerate(rows)
            if site in row_sites.get(position, (site,))
        ]


# ---- Reading a scenario ------------------------------------------------------


def scenario_name(path: Path) -> str:
    """Name a scenario after its file (without extension) or directory."""
    path = Path(path)
    return path.name if path.is_dir() else path.stem


def output_folder_name(path: Path) -> str:
    """Name the folder, next to the scenario, that its dataset is written to.

    A file scenario ``linkage.yml`` writes ``linkage/``. A directory scenario
    ``boston/`` can't write into itself, so it writes ``boston_output/``.
    """
    name = scenario_name(path)
    return f"{name}_output" if Path(path).is_dir() else name


def load_scenario(path: Path) -> Scenario:
    """Read a scenario from a YAML file, or a directory of numbered YAML files."""
    scenario_yaml = read_yaml(path)
    check_top_level_keys(scenario_yaml)
    values = scenario_yaml.values
    seed = read_section(read_seed, values, "seed", required=True)
    scale = read_value(int, values, "scale", default=1)
    sites = read_section(read_sites, values, "sites", required=True)
    outputs = read_section(read_outputs, values, "outputs", required=True)

    groups = read_section(
        lambda groups: read_generated_groups(groups, sites, scale),
        values,
        "generated_individuals",
    )
    uses_modules = any(group.modules for group in groups)

    row_names = read_row_names(scenario_yaml)
    shared_rows, shared_row_sites = read_shared_tables(scenario_yaml, row_names, sites)
    if uses_modules:
        add_module_vocabulary(shared_rows, row_names)

    described = read_individuals(scenario_yaml, sites, row_names)
    generated = generate_individuals(groups, next_individual_id(described))
    individuals = described + generated
    check_unique_source_values(individuals)
    person_ids = read_section(read_person_ids, values, "person_ids")
    if person_ids == "per_site":
        check_scrambled_ids(individuals)
    person_locations = read_value(bool, values, "person_locations", default=False)

    attribute_pools = read_section(
        lambda columns: read_attribute_pools(columns, row_names),
        values,
        "person_attributes",
    )
    pii_variation = read_section(read_pii_variation, values, "pii")
    relatives = read_section(read_relatives, values, "relatives")
    birth_years = read_section(read_year_span, values, "birth_years")
    measurement_years = read_section(
        read_year_span, values, "measurement_years", required=uses_modules
    )
    event_sharing = EventSharing(
        rates=read_section(read_event_sharing, values, "event_sharing"),
        same_month=read_rate(values, "same_month"),
        coverage=read_section(
            lambda mapping: read_site_coverage(mapping, sites),
            values,
            "site_coverage",
        ),
    )

    return Scenario(
        name=scenario_name(path),
        seed=seed,
        sites=sites,
        outputs=outputs,
        individuals=individuals,
        row_names=row_names,
        shared_rows=shared_rows,
        attribute_pools=attribute_pools,
        pii_variation=pii_variation,
        shared_row_sites=shared_row_sites,
        person_ids=person_ids,
        person_locations=person_locations,
        birth_years=birth_years,
        measurement_years=measurement_years,
        event_sharing=event_sharing,
        relatives=relatives,
        generated=generated,
    )


def check_top_level_keys(scenario_yaml: ScenarioYaml) -> None:
    """Check that each top-level key is a scenario key or a shared table's name."""
    reserved = sorted(RESERVED_KEYS & scenario_yaml.values.keys())
    if reserved:
        raise ScenarioError(", ".join(reserved), msg.SCENARIO_RESERVED_KEYS())
    for key in scenario_yaml.values:
        with within(key):
            if key in SCENARIO_KEYS:
                continue
            table = table_for_yaml_name(key)
            if table is None:
                unknown_table_error(key)
            if table.is_person_level:
                scenario_problem(msg.SCENARIO_PERSON_ROWS_AT_TOP(key=key))


def unknown_table_error(key: str) -> NoReturn:
    """Report a full CDM table name, with the name to write instead."""
    table = table_with_cdm_name(key)
    if table is None:
        unknown_key_error()
    scenario_problem(msg.SCENARIO_SHORT_TABLE_NAME(short=table.yaml_name, table=key))


def read_seed(value: object) -> int | str:
    """Read the scenario's seed, which is a whole number or text."""
    if isinstance(value, bool) or not isinstance(value, (int, str)) or value == "":
        scenario_problem(msg.SCENARIO_BAD_SEED(value=_wording.shown(value)))
    return value


def read_sites(items: object) -> list:
    """Read the scenario's sites."""
    sites = [str(site) for site in as_list(items)]
    if not sites:
        scenario_problem(msg.SCENARIO_NO_SITES())
    for position, site in enumerate(sites, start=1):
        with within(f"[{position}]"):
            try:
                validated_site(site)
            except ValueError:
                scenario_problem(msg.SCENARIO_INVALID_SITE(site=site))
            if site in sites[: position - 1]:
                scenario_problem(msg.SCENARIO_SITE_TWICE(site=site))
    return sites


def site_names(items: object, known_sites: list[str]) -> list:
    """Read a list of sites, each of which must be one of ``known_sites``."""
    sites = [str(site) for site in as_list(items)]
    for site in sites:
        if site not in known_sites:
            scenario_problem(
                msg.SCENARIO_UNKNOWN_SITE(site=site, sites=_wording.listed(known_sites))
            )
    return sites


def read_outputs(items: object) -> list:
    """Read the outputs to write, each of which must be one of OUTPUTS."""
    outputs = [str(output) for output in as_list(items)]
    if not outputs:
        scenario_problem(msg.SCENARIO_NO_OUTPUTS())
    for output in outputs:
        if output not in OUTPUTS:
            scenario_problem(msg.SCENARIO_UNKNOWN_OUTPUT(value=output))
    return outputs


def read_row(table: OmopTable, mapping: dict, row_names: RowNames) -> OmopRow:
    """Read a row of ``table`` from its YAML ``mapping``."""
    row = OmopRow()
    foreach_entry(
        lambda key, value: set_yaml_column(row, table, key, value, row_names), mapping
    )
    return row


# ---- Shared tables: concepts, locations, care sites, ... --------------------


def shared_table_keys(scenario_yaml: ScenarioYaml) -> list:
    """List the scenario's keys that name shared tables, sorted."""
    return sorted(key for key in scenario_yaml.values if key not in SCENARIO_KEYS)


def shared_row_id(table: OmopTable, mapping: dict, position: int) -> int | None:
    """Return a shared row's id: the one in the YAML, or its position in the list."""
    key = table.primary_key
    if key is None or table.column(key).type is not int:
        return None
    yaml_key = table.column(key).yaml_name
    return read_value(int, mapping, yaml_key) if yaml_key in mapping else position


def read_row_names(scenario_yaml: ScenarioYaml) -> RowNames:
    """Collect every shared row's name before any row is read.

    Rows refer to each other by name (see ``OmopTable.name_column``).
    """
    row_names = RowNames()
    for key in shared_table_keys(scenario_yaml):
        table = known_table(key)  # Checked by check_top_level_keys
        if table.name_column is None:
            continue
        name_key = table.column(table.name_column).yaml_name
        ids_by_name = row_names[table.name] = {}

        def collect(
            item: object,
            position: int,
            table: OmopTable = table,
            name_key: str = name_key,
            ids: dict = ids_by_name,
        ) -> None:
            mapping = as_mapping(item)
            if name_key not in mapping:
                return
            name = str(mapping[name_key])
            if name in ids:
                raise ScenarioError(name_key, msg.SCENARIO_NAME_TAKEN(value=name))
            ids[name] = shared_row_id(table, mapping, position)

        map_list_items(collect, scenario_yaml, key)
    return row_names


def read_shared_tables(
    scenario_yaml: ScenarioYaml, row_names: RowNames, sites: list[str]
) -> tuple[dict, dict]:
    """Read every shared table's rows, and the sites of rows that list ``sites``.

    Return ``(rows_by_table, sites_by_table)`` (see ``Scenario.shared_row_sites``).
    """
    rows_by_table = {}
    sites_by_table = {}
    for key in shared_table_keys(scenario_yaml):
        table = known_table(key)  # Checked by check_top_level_keys
        read = map_list_items(
            lambda item, position, table=table: read_shared_row(
                table, item, position, row_names, sites
            ),
            scenario_yaml,
            key,
        )
        rows = [row for row, _ in read]
        if table.name == "cdm_source" and len(rows) > 1:
            raise ScenarioError(key, msg.SCENARIO_ONE_ROW())
        rows_by_table[table.name] = rows
        row_sites = {
            position: row_sites
            for position, (_, row_sites) in enumerate(read)
            if row_sites is not None
        }
        if row_sites:
            sites_by_table[table.name] = row_sites
    return rows_by_table, sites_by_table


def read_shared_row(
    table: OmopTable, item: object, position: int, row_names: RowNames, sites: list[str]
) -> tuple[OmopRow, list[str] | None]:
    """Read one shared row, and the sites that write it (None for every site).

    ``concept_ancestor`` rows give only direct ancestor-descendant pairs. The
    full hierarchy is built from them.
    """
    if table.name == "concept_ancestor":
        item = check_keys(item, ("ancestor_concept_id", "descendant_concept_id"))
        required_value(item, "ancestor_concept_id")
        required_value(item, "descendant_concept_id")
    mapping = as_mapping(item)
    row_sites = None
    if "sites" in mapping:
        row_sites = read_section(
            lambda items: site_names(items, sites), mapping, "sites"
        )
        mapping = {key: value for key, value in mapping.items() if key != "sites"}
    row = read_row(table, mapping, row_names)
    row_id = shared_row_id(table, mapping, position)
    if row_id is not None:
        row[table.primary_key] = row_id
    return row, row_sites


# ---- Individuals and their events -------------------------------------------


def next_individual_id(individuals: list) -> int:
    """Return the next individual's id, after every one so far."""
    return max((i.individual_id for i in individuals), default=0) + 1


def read_individuals(
    scenario_yaml: ScenarioYaml, sites: list[str], row_names: RowNames
) -> list:
    """Read the described individuals, numbering those without an ``id``.

    Each individual without an ``id`` gets the next number after the highest
    id so far.
    """
    individuals = []

    def read_next(entry: object, _position: int) -> None:
        individual = read_individual(
            entry, next_individual_id(individuals), sites, row_names
        )
        if any(
            earlier.individual_id == individual.individual_id for earlier in individuals
        ):
            where = "id"
            raise ScenarioError(
                where, msg.SCENARIO_ID_TAKEN(value=individual.individual_id)
            )
        individuals.append(individual)

    map_list_items(read_next, scenario_yaml, "individuals")
    return individuals


def read_individual(
    entry: object, default_id: int, sites: list[str], row_names: RowNames
) -> Individual:
    """Read one described individual.

    An individual's keys are person columns (``source_value``,
    ``gender_concept_id``, ...), person-level tables listing that
    individual's events (``condition``, ``measurement``, ...), PII keys
    (``first_name``, ``city``, ...), or ``description``, ``expected``,
    ``sites`` and ``at_sites``.
    """
    entry = as_mapping(entry)
    required_value(entry, "source_value")
    person_columns = OmopRow()
    pii = {}
    events = []

    def read_entry(key: str, value: object) -> None:
        if key in PII_KEYS:
            pii[key] = read_pii_value(key, value)
            return
        if key in INDIVIDUAL_KEYS:
            return
        if key == "birth_datetime":
            scenario_problem(msg.SCENARIO_BIRTH_DATETIME())
        table = table_for_yaml_name(key)
        if table is None:
            if table_with_cdm_name(key) is not None:
                unknown_table_error(key)
            set_yaml_column(person_columns, PERSON_TABLE, key, value, row_names)
            return
        check_event_table(table)
        events.extend(map_items(lambda item: read_event(table, item, row_names), value))

    foreach_entry(read_entry, entry)
    if person_columns["person_source_value"] is None:
        scenario_problem(msg.SCENARIO_NULL_SOURCE_VALUE())
    individual_sites = read_section(
        lambda items: list(sites) if items is None else site_names(items, sites),
        entry,
        "sites",
    )
    return Individual(
        individual_id=read_value(int, entry, "id", default=default_id),
        person_source_value=person_columns["person_source_value"],
        sites=individual_sites,
        description=str(entry.get("description") or ""),
        expected={
            str(group): as_mapping(by_year)
            for group, by_year in as_mapping(entry.get("expected")).items()
        },
        person_columns=person_columns,
        pii=pii,
        at_sites=read_section(
            lambda mapping: read_at_sites(mapping, individual_sites, sites, row_names),
            entry,
            "at_sites",
        ),
        events=checked_visits(events),
    )


def read_pii_value(key: str, value: object) -> str | None:
    """Read a PII value stated in the YAML.

    ``null`` means the field is left empty.
    """
    if value is None:
        return None
    if key == "zip" and not isinstance(value, str):
        scenario_problem(msg.SCENARIO_ZIP_UNQUOTED())
    return yaml_to(str, value)


def read_at_sites(
    mapping: object,
    individual_sites: list[str],
    all_sites: list[str],
    row_names: RowNames,
) -> dict:
    """Read ``at_sites``: for each site, the exact values that site records.

    Each key is a PII key or a person column. Columns that identify the
    individual or give their date of birth can't differ between sites.
    """
    differences_by_site = {}

    def read_site(site: str, entries: object) -> None:
        if site not in all_sites:
            scenario_problem(
                msg.SCENARIO_UNKNOWN_SITE(site=site, sites=_wording.listed(all_sites))
            )
        if site not in individual_sites:
            scenario_problem(msg.SCENARIO_NOT_AT_SITE(site=site))
        differences = differences_by_site[site] = SiteDifferences()

        def read_difference(key: str, value: object) -> None:
            if key in PII_KEYS:
                differences.pii[key] = read_pii_value(key, value)
                return
            column = PERSON_TABLE.column_for_yaml_name(key)
            if column is None and PERSON_TABLE.has_column(key):
                yaml_column(PERSON_TABLE, key)  # Raises with the name to use instead
            if column is None:
                scenario_problem(
                    msg.SCENARIO_NOT_PII_OR_PERSON(pii_keys=_wording.listed(PII_KEYS))
                )
            if column.name in FIXED_PERSON_COLUMNS:
                scenario_problem(msg.SCENARIO_FIXED_PERSON_COLUMN())
            set_omop_value(
                differences.person_columns, PERSON_TABLE, column.name, value, row_names
            )

        foreach_entry(read_difference, entries)

    foreach_entry(read_site, mapping)
    return differences_by_site


def check_event_table(table: OmopTable) -> None:
    """Check that an individual's event may be in ``table``."""
    if table.name == "person":
        scenario_problem(msg.SCENARIO_PERSON_TABLE())
    if table.name == "observation_period":
        scenario_problem(msg.SCENARIO_OBSERVATION_PERIOD())
    if table.name == "visit_occurrence":
        scenario_problem(msg.SCENARIO_VISIT_TABLE())
    if not table.is_person_level:
        scenario_problem(msg.SCENARIO_SHARED_TABLE_ON_INDIVIDUAL(table=table.yaml_name))


def read_event(table: OmopTable, entry: object, row_names: RowNames) -> ClinicalEvent:
    """Read one event, with the columns of the visit it belongs to.

    An event's keys are columns of its own table. For tables linked to
    visits, there is also ``visit``, which holds columns of the event's
    visit (``concept_id``, ``care_site_id``, ...).
    """
    columns = OmopRow()
    visit_columns = OmopRow()

    def read_entry(key: str, value: object) -> None:
        if key == "visit":
            if not table.links_to_visit:
                scenario_problem(msg.SCENARIO_NO_VISITS(table=table.yaml_name))
            visit_columns.update(read_visit_columns(value, row_names))
            return
        if yaml_column(table, key).name in LINKING_EVENT_COLUMNS:
            scenario_problem(msg.SCENARIO_SET_BY_SYNTH())
        set_yaml_column(columns, table, key, value, row_names)

    foreach_entry(read_entry, entry)
    start_date = table.start_date_column
    if start_date is not None and not _is_date(columns.get(start_date)):
        scenario_problem(
            msg.SCENARIO_NO_START_DATE(key=table.column(start_date).yaml_name)
        )
    return ClinicalEvent(table.name, columns, visit_columns)


def _is_date(value: object) -> bool:
    return type(value) is date


def read_visit_columns(mapping: object, row_names: RowNames) -> OmopRow:
    """Read the columns given for an event's visit, except those synth sets itself."""
    visit_columns = OmopRow()

    def read_entry(key: str, value: object) -> None:
        if yaml_column(VISIT_TABLE, key).name in ("visit_occurrence_id", "person_id"):
            scenario_problem(msg.SCENARIO_SET_BY_SYNTH())
        set_yaml_column(visit_columns, VISIT_TABLE, key, value, row_names)

    foreach_entry(read_entry, mapping)
    return visit_columns


def checked_visits(events: list) -> list:
    """Check that events on the same date agree on their visit, and return them."""
    plan_visits(events)
    return events


def plan_visits(events: list[ClinicalEvent]) -> list:
    """Group visit-linked events into one visit per date.

    The visit's columns come from the ``visit`` entries of those events,
    which must agree. A visit whose events give no ``concept_id`` is an
    outpatient visit (concept 9202). Return ``[(date, visit columns)]`` in
    date order.
    """
    visit_on = {}
    for event in events:
        if not omop_table(event.table).links_to_visit:
            continue
        visit_date = event.start_date
        visit = visit_on.setdefault(visit_date, OmopRow())
        for column, value in event.visit.items():
            if column in visit and visit[column] != value:
                with within("visit"):
                    scenario_problem(
                        msg.SCENARIO_VISIT_CONFLICT(
                            date=visit_date,
                            column=VISIT_TABLE.column(column).yaml_name,
                            first=visit[column],
                            second=value,
                        )
                    )
            visit[column] = value
    for visit_date, visit in visit_on.items():
        if "visit_concept_id" not in visit:
            set_omop_value(
                visit, VISIT_TABLE, "visit_concept_id", DEFAULT_VISIT_CONCEPT, {}
            )
        visit.setdefault("visit_start_date", visit_date)
    return sorted(visit_on.items(), key=lambda pair: pair[0])


def check_unique_source_values(individuals: list) -> None:
    """Check that no two individuals have the same person_source_value."""
    seen = set()
    for individual in individuals:
        value = individual.person_source_value
        if value in seen:
            where = "individuals"
            raise ScenarioError(where, msg.SCENARIO_SOURCE_VALUE_TAKEN(value=value))
        seen.add(value)


# ---- Generated individuals ---------------------------------------------------


def read_generated_groups(items: object, sites: list[str], scale: int) -> list:
    """Read the generated groups. Each entry is one group.

    A group is ``count`` individuals with records at ``sites``, and the
    clinical ``modules`` that draw their events.

    Every count is multiplied by ``scale``.
    """

    def read_group(entry: object) -> GeneratedGroup:
        entry = check_keys(entry, ("sites", "count", "modules"))
        group_sites = read_section(
            lambda items: site_names(items, sites), entry, "sites", required=True
        )
        modules = read_section(read_module_uses, entry, "modules")
        return GeneratedGroup(
            group_sites, read_value(int, entry, "count") * scale, modules
        )

    return map_items(read_group, items)


def read_module_uses(mapping: object) -> list:
    """Read ``modules``: each key is a module, with its ``prevalence`` (default 1)."""
    uses = []

    def read_use(name: str, settings: object) -> None:
        if name not in MODULES:
            scenario_problem(
                msg.SCENARIO_UNKNOWN_MODULE(
                    value=name, modules=_wording.listed(MODULES)
                )
            )
        settings = check_keys(settings, ("prevalence",))
        prevalence = read_rate(settings, "prevalence") if settings else 1.0
        uses.append(ModuleUse(name, prevalence))

    foreach_entry(read_use, mapping)
    return uses


def generate_individuals(groups: list, first_individual_id: int) -> list:
    """Generate individuals, numbered consecutively and named ``P0000042``."""
    individuals = []
    for group in groups:
        for _ in range(group.count):
            individual_id = first_individual_id + len(individuals)
            individuals.append(
                Individual(
                    individual_id=individual_id,
                    person_source_value=f"P{individual_id:07d}",
                    sites=list(group.sites),
                    modules=list(group.modules),
                )
            )
    return individuals


def add_module_vocabulary(shared_rows: dict, row_names: RowNames) -> None:
    """Add the concepts that clinical modules use (``MODULE_CONCEPTS``).

    A concept the scenario already names must have the module's id. If the
    scenario gives a module concept's id a different name, the concept is
    known by both names, without a second ``concept`` row. Ancestor pairs
    are added unless the scenario already lists them.
    """
    concept_ids = row_names.setdefault("concept", {})
    concept_rows = shared_rows.setdefault("concept", [])
    given_ids = {row["concept_id"] for row in concept_rows}
    for name, concept_id in MODULE_CONCEPTS.items():
        if name in concept_ids and concept_ids[name] != concept_id:
            where = "concept"
            raise ScenarioError(
                where,
                msg.SCENARIO_MODULE_CONCEPT_ID(
                    value=name, expected=concept_id, found=concept_ids[name]
                ),
            )
        concept_ids[name] = concept_id
        if concept_id not in given_ids:
            concept_rows.append({"concept_id": concept_id, "concept_name": name})
            given_ids.add(concept_id)
    pairs = shared_rows.setdefault("concept_ancestor", [])
    given_pairs = {
        (row["ancestor_concept_id"], row["descendant_concept_id"]) for row in pairs
    }
    for ancestor, descendant in MODULE_CONCEPT_ANCESTORS:
        pair = (MODULE_CONCEPTS[ancestor], MODULE_CONCEPTS[descendant])
        if pair not in given_pairs:
            pairs.append(
                {"ancestor_concept_id": pair[0], "descendant_concept_id": pair[1]}
            )


def read_person_ids(value: object) -> str:
    """Read ``person_ids``: ``shared`` (the default) or ``per_site``."""
    mode = "shared" if value is None else str(value)
    if mode not in PERSON_ID_MODES:
        scenario_problem(
            msg.SCENARIO_PERSON_IDS(modes=_wording.listed(PERSON_ID_MODES))
        )
    return mode


def check_scrambled_ids(individuals: list) -> None:
    """Check that every individual's id can be scrambled."""
    for individual in individuals:
        if not 1 <= individual.individual_id <= SCRAMBLED_ID_LIMIT:
            where = "person_ids"
            raise ScenarioError(
                where,
                msg.SCENARIO_ID_RANGE(
                    limit=SCRAMBLED_ID_LIMIT, value=individual.individual_id
                ),
            )


# ---- Generated histories -----------------------------------------------------


def read_year_span(value: object) -> tuple | None:
    """Read ``[first, last]``: two years, with the first no later than the last."""
    if value is None:
        return None
    years = [yaml_to(int, year) for year in as_list(value)]
    match years:
        case [first, last] if first <= last:
            return first, last
    return scenario_problem(msg.SCENARIO_YEAR_SPAN())


def read_event_sharing(mapping: object) -> list:
    """Read the chances for ``one_site``, ``several_sites`` and ``all_sites``.

    The chances must add up to 1. Without the section, every event is
    recorded at all of its sites.
    """
    if mapping is None:
        return EventSharing().rates
    mapping = check_keys(mapping, EVENT_SHARING_KEYS)
    rates = [read_rate(mapping, key) for key in EVENT_SHARING_KEYS]
    if not math.isclose(sum(rates), 1.0, rel_tol=0, abs_tol=1e-9):
        scenario_problem(msg.SCENARIO_SHARING_TOTAL(total=f"{sum(rates):g}"))
    return rates


def read_relatives(mapping: object) -> dict:
    """Read ``relatives``: the chance of each kind of relative.

    For each kind, this is the chance that a generated individual is that
    kind of relative of an earlier one.
    """
    if mapping is None:
        return {}
    mapping = check_keys(mapping, RELATIVE_KEYS)
    rates = {key: read_rate(mapping, key) for key in RELATIVE_KEYS}
    if sum(rates.values()) > 1.0 + 1e-9:
        scenario_problem(msg.SCENARIO_RELATIVES_TOTAL(total=f"{sum(rates.values()):g}"))
    return rates


def read_site_coverage(mapping: object, sites: list[str]) -> dict:
    """Read the site coverage: each key is a site, with the modules it records."""
    coverage = {}

    def read_site(site: str, modules: object) -> None:
        if site not in sites:
            scenario_problem(
                msg.SCENARIO_UNKNOWN_SITE(site=site, sites=_wording.listed(sites))
            )

        def read_module(name: str) -> str:
            name = str(name)
            if name not in MODULES:
                scenario_problem(
                    msg.SCENARIO_UNKNOWN_MODULE(
                        value=name, modules=_wording.listed(MODULES)
                    )
                )
            return name

        coverage[site] = set(map_items(read_module, modules))

    foreach_entry(read_site, mapping)
    return coverage


# ---- Variation between sites -------------------------------------------------


def read_attribute_pools(columns: object, row_names: RowNames) -> list:
    """Read ``person_attributes``: each key is a person column with a weighted pool."""
    pools = []

    def read_pool(key: str, spec: object) -> None:
        column = yaml_column(PERSON_TABLE, key).name
        if column in FIXED_PERSON_COLUMNS:
            scenario_problem(msg.SCENARIO_FIXED_PERSON_COLUMN())
        spec = check_keys(spec, ("change_rate", "pool"))

        def read_choices(items: object) -> list:
            if not as_list(items):
                scenario_problem(msg.SCENARIO_EMPTY_POOL())
            return map_items(
                lambda choice: read_weighted_choice(choice, column, row_names), items
            )

        choices = read_section(read_choices, spec, "pool", required=True)
        change_rate = read_rate(spec, "change_rate")
        if change_rate >= 1.0 and len({repr(value) for value, _ in choices}) <= 1:
            scenario_problem(msg.SCENARIO_POOL_TOO_SMALL())
        pools.append(
            AttributePool(
                column,
                change_rate,
                [value for value, _ in choices],
                [weight for _, weight in choices],
            )
        )

    foreach_entry(read_pool, columns)
    return pools


def read_weighted_choice(
    choice: object, column: str, row_names: RowNames
) -> tuple[object, int]:
    """Read one possible value for a person column, and its weight."""
    choice = check_keys(choice, ("value", "weight"))
    value = required_value(choice, "value")
    with within("value"):
        check_omop_value(PERSON_TABLE, column, value, row_names)
    weight = read_value(int, choice, "weight")
    if weight < 1:
        where = "weight"
        raise ScenarioError(where, msg.SCENARIO_BAD_WEIGHT(value=weight))
    return value, weight


def read_pii_variation(pii: object) -> PiiVariation:
    """Read the ``pii`` section: how sites' copies of names and addresses differ."""
    pii = check_keys(pii, ("name", "address", "shuffle_rows"))
    name = read_section(
        lambda mapping: read_text_variation(
            mapping, ("typos", "case_change", "missing", "transliteration")
        ),
        pii,
        "name",
    )
    address = read_section(
        lambda mapping: read_text_variation(
            mapping, ("change_rate", "typos", "case_change", "missing")
        ),
        pii,
        "address",
    )
    address_change_rate = read_section(
        lambda mapping: read_rate(as_mapping(mapping), "change_rate"), pii, "address"
    )
    shuffle_rows = read_value(bool, pii, "shuffle_rows", default=False)
    return PiiVariation(name, address_change_rate, address, shuffle_rows)


def read_text_variation(mapping: object, allowed_keys: Iterable[str]) -> TextVariation:
    """Read how a site's copy of a text field can differ."""
    mapping = check_keys(mapping, allowed_keys)
    return TextVariation(
        typos=read_section(read_typos, mapping, "typos"),
        case_change=read_rate(mapping, "case_change"),
        missing=read_rate(mapping, "missing"),
        transliteration=read_rate(mapping, "transliteration"),
    )


def read_typos(typos: object) -> Typos:
    """Read ``typos``. If it is missing or an empty mapping, there are no typos."""
    typos = check_keys(typos, ("rate", "max_edits"))
    max_edits = read_value(int, typos, "max_edits", default=1)
    if max_edits < 1:
        where = "max_edits"
        raise ScenarioError(where, msg.SCENARIO_BAD_MAX_EDITS(value=max_edits))
    return Typos(read_rate(typos, "rate"), max_edits)
