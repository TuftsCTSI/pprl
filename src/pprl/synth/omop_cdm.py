"""Define the OMOP CDM v5.4 tables, and how a scenario's YAML names them.

Tables and columns are read from the CDM's field-level CSV file, which
ships with this package.

A scenario's YAML names tables and columns by two rules:

- A table ending in ``_occurrence`` or ``_exposure`` is written without that
  ending (``condition``, ``drug``, ``procedure``, ``device``, ``visit``).
  Every other table keeps its CDM name.
- A column that starts with its table's name is written without it:
  ``condition.start_date`` is condition_occurrence.condition_start_date,
  ``drug.start_date`` is drug_exposure.drug_exposure_start_date, and
  ``person.id`` is person.person_id. Every other column keeps its CDM name.

>>> omop_table("condition_occurrence").yaml_name
'condition'
>>> omop_table("drug_exposure").column("drug_exposure_start_date").yaml_name
'start_date'
>>> omop_table("person").column_for_yaml_name("source_value").name
'person_source_value'
"""

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from pprl._io import for_reading

CDM_SPEC_PATH = Path(__file__).parent / "cdm" / "OMOP_CDMv5.4_Field_Level.csv"

# Tables in the CDM's field-level file that synth does not write.
EXCLUDED_TABLES = ("cohort", "cohort_definition")

# One row of an OMOP table, mapping column name to value. A column set to
# None is present but empty. A column that is absent has not been set yet.
OmopRow = dict


@dataclass(frozen=True)
class OmopColumn:
    """One CDM column. ``refers_to`` is the table that a foreign key refers to."""

    name: str
    yaml_name: str
    type: type
    required: bool
    primary_key: bool
    refers_to: str | None


@dataclass(frozen=True)
class OmopTable:
    """An OMOP CDM table: its name, its name in a scenario's YAML, and its columns."""

    name: str
    yaml_name: str
    columns: tuple[OmopColumn, ...]

    def has_column(self, name: str) -> bool:
        """Tell whether the table has a column ``name``."""
        return any(column.name == name for column in self.columns)

    def column(self, name: str) -> OmopColumn:
        """Return the table's column ``name``."""
        return next(column for column in self.columns if column.name == name)

    def column_for_yaml_name(self, key: str) -> OmopColumn | None:
        """Find the column a scenario's YAML key names in this table, or None."""
        return next((c for c in self.columns if c.yaml_name == key), None)

    @property
    def primary_key(self) -> str | None:
        """Return the name of the table's primary key column, or None."""
        return next((c.name for c in self.columns if c.primary_key), None)

    @property
    def start_date_column(self) -> str | None:
        """Return the start date column, which is the table's first date column."""
        return next((c.name for c in self.columns if c.type is date), None)

    @property
    def is_person_level(self) -> bool:
        """Tell whether rows are about one person. Other tables are shared."""
        return self.has_column("person_id")

    @property
    def links_to_visit(self) -> bool:
        """Tell whether the table's rows refer to a visit."""
        return self.has_column("visit_occurrence_id")

    @property
    def name_column(self) -> str | None:
        """Return the column that other rows use to refer to this table's rows by name.

        This is ``concept_name`` for concepts. For other tables it is the
        table's ``*_source_value`` (as for locations and care sites). Return
        None if rows of the table can't be referred to by name.
        """
        if self.name == "concept":
            return "concept_name"
        source_value = f"{self.name}_source_value"
        return source_value if self.has_column(source_value) else None


def yaml_table_name(table_name: str) -> str:
    """Return the name that a scenario's YAML gives a table.

    That is the CDM name without _occurrence or _exposure.
    """
    return re.sub(r"_(occurrence|exposure)$", "", table_name)


def yaml_column_name(table_name: str, column_name: str) -> str:
    """Return the name a scenario's YAML gives a column, without the table's name.

    >>> yaml_column_name("visit_occurrence", "visit_start_date")
    'start_date'
    >>> yaml_column_name("measurement", "value_as_number")
    'value_as_number'
    """
    for prefix in (f"{table_name}_", f"{yaml_table_name(table_name)}_"):
        if column_name.startswith(prefix):
            return column_name.removeprefix(prefix)
    return column_name


_PYTHON_TYPES = {"integer": int, "float": float, "date": date, "datetime": datetime}


def _python_type(cdm_type: str) -> type:
    cdm_type = cdm_type.lower()
    if cdm_type.startswith("varchar"):
        return str
    if cdm_type not in _PYTHON_TYPES:
        msg = f"OMOP CDM spec: unknown datatype '{cdm_type}'"
        raise ValueError(msg)
    return _PYTHON_TYPES[cdm_type]


_BOOLEANS = {"TRUE": True, "FALSE": False}


def _boolean(cdm_value: str) -> bool:
    if cdm_value not in _BOOLEANS:
        msg = f"OMOP CDM spec: unknown boolean '{cdm_value}'"
        raise ValueError(msg)
    return _BOOLEANS[cdm_value]


def _foreign_table(cdm_value: str) -> str:
    """Return the table that a foreign key refers to, in lower case.

    Return an empty string if the value is NA.
    """
    return "" if cdm_value == "NA" else cdm_value.lower()


def _read_cdm_spec(path: Path) -> tuple[OmopTable, ...]:
    columns_by_table: dict[str, list[OmopColumn]] = {}
    with for_reading(path, newline="") as spec:
        for fields in csv.DictReader(spec):
            table_name = fields["cdmTableName"]
            if table_name in EXCLUDED_TABLES:
                continue
            column_name = fields["cdmFieldName"].strip('"')  # "offset" may be quoted
            foreign_key = _boolean(fields["isForeignKey"])
            columns_by_table.setdefault(table_name, []).append(
                OmopColumn(
                    name=column_name,
                    yaml_name=yaml_column_name(table_name, column_name),
                    type=_python_type(fields["cdmDatatype"]),
                    required=_boolean(fields["isRequired"]),
                    primary_key=_boolean(fields["isPrimaryKey"]),
                    refers_to=(
                        _foreign_table(fields["fkTableName"]) if foreign_key else None
                    ),
                )
            )
    return tuple(
        OmopTable(name, yaml_table_name(name), tuple(columns))
        for name, columns in columns_by_table.items()
    )


OMOP_TABLES = _read_cdm_spec(CDM_SPEC_PATH)
_TABLES_BY_NAME = {table.name: table for table in OMOP_TABLES}


def omop_table(name: str) -> OmopTable:
    """Return the table named ``name``."""
    return _TABLES_BY_NAME[name]


def table_for_yaml_name(key: str) -> OmopTable | None:
    """Find the table that a scenario's YAML key names, or return None."""
    return next((table for table in OMOP_TABLES if table.yaml_name == key), None)


def known_table(key: str) -> OmopTable:
    """Return the table that a scenario's YAML key names. The key must be checked."""
    table = table_for_yaml_name(key)
    if table is None:
        raise AssertionError(key)
    return table


def table_with_cdm_name(key: str) -> OmopTable | None:
    """Find the table whose full CDM name is ``key``, or return None.

    This is used to suggest the YAML name.
    """
    return _TABLES_BY_NAME.get(key)


def is_concept_column(name: str) -> bool:
    """Tell whether the column ``name`` holds a concept id."""
    return name.endswith("_concept_id")


PERSON_TABLE = omop_table("person")
VISIT_TABLE = omop_table("visit_occurrence")
