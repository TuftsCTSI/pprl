"""Read a scenario's YAML.

This module loads files (one file, or a directory of numbered files read as
one). It reports where in the YAML an error occurred, checks shapes, and
turns a YAML value into an OMOP column value (``set_omop_value``).
"""

import contextlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast, overload

from pprl import _messages as msg
from pprl import _wording
from pprl._io import YamlParseError, load_yaml
from pprl._shared import UserFacingError, is_whole_number

from .omop_cdm import (
    OmopColumn,
    OmopRow,
    OmopTable,
    is_concept_column,
    omop_table,
    yaml_table_name,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable

# ---- Errors that point at the offending YAML entry ---------------------------


class ScenarioError(UserFacingError):
    """A problem with the scenario, with its location in the YAML.

    The message comes from the message catalog. The location goes before it.

    >>> print(ScenarioError("individuals[3].condition[1]", "missing `start_date`"))
    individuals[3].condition[1]: missing `start_date`
    """

    def __init__(self, location: str, message: str) -> None:
        super().__init__(location, message)
        self.location = location
        self.message = message

    def __str__(self) -> str:
        """Return the problem, after its location in the scenario, if it has one."""
        return f"{self.location}: {self.message}" if self.location else self.message

    def inside(self, key: str) -> ScenarioError:
        """Return the error with its location placed inside ``key``."""
        location = str(key)
        if self.location:
            separator = "" if self.location.startswith("[") else "."
            location = f"{location}{separator}{self.location}"
        return ScenarioError(location, self.message)


def scenario_problem(message: str) -> NoReturn:
    """Raise ScenarioError with ``message``, for the caller's ``within`` to locate."""
    nowhere = ""  # The callers add the location (see within).
    raise ScenarioError(nowhere, message)


def unknown_key_error() -> NoReturn:
    """Raise ScenarioError for a key the scenario does not know."""
    scenario_problem(msg.SCENARIO_UNKNOWN_KEY())


@contextlib.contextmanager
def within(key: str) -> Generator[None]:
    """Locate any ScenarioError raised in the block inside ``key``.

    Nested blocks build the full location, such as
    ``individuals[3].condition[1].start_date``.
    """
    try:
        yield
    except ScenarioError as error:
        raise error.inside(key) from None


# ---- Loading -----------------------------------------------------------------


@dataclass
class ScenarioYaml:
    """A scenario's YAML, from one file or merged from a directory of files.

    ``item_locations[key]`` records where each item of the list under ``key``
    was written (``02_patients.yml: individuals[3]``), for error messages.
    """

    values: dict = field(default_factory=dict)
    item_locations: dict = field(default_factory=dict)


def read_yaml(path: Path) -> ScenarioYaml:
    """Read a scenario from a YAML file, or from a directory of numbered files.

    A list (such as ``individuals``) may continue across files. Any other key
    may appear in only one file.
    """
    path = Path(path)
    files = yaml_files_in_load_order(path) if path.is_dir() else [path]
    scenario = ScenarioYaml()
    for file in files:
        label = f"{file.name}: " if path.is_dir() else ""
        try:
            content = load_yaml(file)
        except YamlParseError as error:
            raise ScenarioError(
                file.name, msg.SCENARIO_NOT_YAML(line=error.line, column=error.column)
            ) from None
        if content is None:
            continue
        if not isinstance(content, dict):
            raise ScenarioError(file.name, msg.SCENARIO_NOT_MAPPING())
        for given, value in content.items():
            key = str(given)
            continues_list = isinstance(value, list) and isinstance(
                scenario.values.get(key, []), list
            )
            if key in scenario.values and not continues_list:
                raise ScenarioError(label + key, msg.SCENARIO_KEY_REPEATED())
            if isinstance(value, list):
                scenario.values.setdefault(key, []).extend(value)
                scenario.item_locations.setdefault(key, []).extend(
                    f"{label}{key}[{position}]" for position in range(1, len(value) + 1)
                )
            else:
                scenario.values[key] = value
    return scenario


def yaml_files_in_load_order(directory: Path) -> list[Path]:
    """List a directory's YAML files, by the number each name starts with."""

    def load_order(file: Path) -> int:
        number = re.match(r"[0-9]+", file.name)
        if number is None:
            raise ScenarioError(file.name, msg.SCENARIO_FILE_UNNUMBERED())
        return int(number.group())

    files = [f for f in directory.iterdir() if f.suffix in (".yml", ".yaml")]
    return sorted(files, key=lambda file: (load_order(file), file.name))


def map_list_items(f: Callable, scenario: ScenarioYaml, key: str) -> list:
    """Call ``f(item, position)`` for each item of a top-level list.

    Errors are reported at the file and position where the item was written.
    ``position`` counts items across all files, starting from 1.
    """
    with within(key):
        items = as_list(scenario.values.get(key))
    locations = scenario.item_locations.get(key, [])
    results = []
    for position, item in enumerate(items, start=1):
        with within(locations[position - 1]):
            results.append(f(item, position))
    return results


def foreach_entry(f: Callable[..., object], mapping: object) -> None:
    """Call ``f(key, value)`` for each entry of a YAML mapping, located at that key."""
    for key, value in as_mapping(mapping).items():
        with within(key):
            f(str(key), value)


def map_items(f: Callable, items: object) -> list:
    """Apply ``f`` to each item of a YAML list, located at that item's position."""
    results = []
    for position, item in enumerate(as_list(items), start=1):
        with within(f"[{position}]"):
            results.append(f(item))
    return results


# ---- Shapes ------------------------------------------------------------------


def as_mapping(value: object) -> dict:
    """Read a YAML mapping. Return {} if there is none."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        scenario_problem(msg.SCENARIO_NOT_MAPPING_VALUE())
    return value


def as_list(value: object) -> list:
    """Read a YAML list. Return [] if there is none."""
    if value is None:
        return []
    if not isinstance(value, list):
        scenario_problem(msg.SCENARIO_NOT_LIST())
    return value


def check_keys(mapping: object, allowed_keys: Iterable[str]) -> dict:
    """Check that ``mapping`` uses only ``allowed_keys``, and return it."""
    mapping = as_mapping(mapping)
    for key in mapping:
        if str(key) not in allowed_keys:
            raise ScenarioError(
                str(key),
                msg.SCENARIO_KEY_NOT_ALLOWED(allowed=_wording.listed(allowed_keys)),
            )
    return mapping


def required_value(mapping: dict, key: str) -> object:
    """Read ``mapping[key]``, which the scenario is required to give."""
    if key not in mapping:
        scenario_problem(msg.SCENARIO_MISSING(key=key))
    return mapping[key]


_NO_DEFAULT = object()


@overload
def read_value[T](value_type: type[T], mapping: dict, key: str) -> T: ...
@overload
def read_value[T, D](
    value_type: type[T], mapping: dict, key: str, default: D
) -> T | D: ...
def read_value(
    value_type: type, mapping: dict, key: str, default: object = _NO_DEFAULT
) -> object:
    """Read ``mapping[key]`` as ``value_type``.

    If there is no default, the key is required.
    """
    if key not in mapping:
        if default is _NO_DEFAULT:
            scenario_problem(msg.SCENARIO_MISSING(key=key))
        return default
    with within(key):
        return yaml_to(value_type, mapping[key])


def read_rate(mapping: dict, key: str) -> float:
    """Read a probability between 0 and 1. Return 0 if it is absent."""
    rate = read_value(float, mapping, key, default=0.0)
    if not 0.0 <= rate <= 1.0:
        raise ScenarioError(key, msg.SCENARIO_RATE(value=rate))
    return rate


def read_section[T](
    f: Callable[[object], T], mapping: dict, key: str, *, required: bool = False
) -> T:
    """Read the YAML under ``mapping[key]`` with ``f``, located at that key."""
    if required:
        required_value(mapping, key)
    with within(key):
        return f(mapping.get(key))


# ---- Values ------------------------------------------------------------------


def yaml_to[T](value_type: type[T], value: object) -> T:
    """Convert a YAML value to ``value_type``.

    >>> yaml_to(int, "42"), yaml_to(float, 139), yaml_to(date, "2023-01-05")
    (42, 139.0, datetime.date(2023, 1, 5))
    >>> yaml_to(datetime, "2023-01-05 10:30:00")
    datetime.datetime(2023, 1, 5, 10, 30)
    """
    try:
        return _convert(value_type, value)
    except TypeError, ValueError:
        scenario_problem(
            msg.SCENARIO_WRONG_TYPE(
                value=_wording.shown(value),
                kind=msg.SCENARIO_TYPES[value_type.__name__],
            )
        )


def _convert[T](value_type: type[T], value: object) -> T:
    """Convert ``value`` to ``value_type``, or raise TypeError or ValueError."""
    converter = _CONVERTERS.get(value_type)
    if converter is None or (isinstance(value, bool) and value_type not in (bool, str)):
        raise TypeError
    return cast("T", converter(value))  # _CONVERTERS[T] returns a T


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    raise TypeError


def _to_int(value: object) -> int:
    if isinstance(value, (int, str)):
        return int(value)
    raise TypeError


def _to_float(value: object) -> float:
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError


def _to_str(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        raise TypeError
    return str(value)


def _to_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time())
    if not isinstance(value, str):
        raise TypeError
    if re.search(r"[T ]", value):
        return datetime.fromisoformat(value.replace(" ", "T"))
    return datetime.combine(date.fromisoformat(value), time())


def _to_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError


_CONVERTERS = {
    bool: _to_bool,
    int: _to_int,
    float: _to_float,
    str: _to_str,
    datetime: _to_datetime,
    date: _to_date,
}


# The names a scenario gives to rows of shared tables, by table. Concepts are
# named by `concept_name`, and locations and care sites by `source_value`.
# Other rows use these names in place of the row's id.
RowNames = dict  # table name -> {row name: id}


def id_for_name(row_names: RowNames, table_name: str, value: object) -> int:
    """Return the id of the row of ``table_name`` that ``value`` names, or ``value``."""
    if is_whole_number(value):
        return value
    name = str(value)
    ids = row_names.get(table_name, {})
    if name not in ids:
        scenario_problem(
            msg.SCENARIO_UNKNOWN_NAME(table=yaml_table_name(table_name), value=name)
        )
    return ids[name]


def refers_by_name(column: OmopColumn) -> bool:
    """Tell whether ``column`` refers to a row of a table whose rows have names."""
    return (
        column.type is int
        and column.refers_to is not None
        and omop_table(column.refers_to).name_column is not None
    )


def yaml_column(table: OmopTable, yaml_key: str) -> OmopColumn:
    """Return the column of ``table`` that a YAML key names.

    A full CDM column name is rejected, with the name to write instead.
    """
    column = table.column_for_yaml_name(yaml_key)
    if column is not None:
        return column
    if not table.has_column(yaml_key):
        unknown_key_error()
    scenario_problem(
        msg.SCENARIO_SHORT_COLUMN_NAME(short=table.column(yaml_key).yaml_name)
    )


def set_yaml_column(
    row: OmopRow,
    table: OmopTable,
    yaml_key: str,
    yaml_value: object,
    row_names: RowNames,
) -> OmopRow:
    """Set the column of ``table`` that ``yaml_key`` names in ``row``."""
    return set_omop_value(
        row, table, yaml_column(table, yaml_key).name, yaml_value, row_names
    )


def set_omop_value(
    row: OmopRow,
    table: OmopTable,
    column_name: str,
    yaml_value: object,
    row_names: RowNames,
) -> OmopRow:
    """Set an OMOP value from a YAML value. This is the only place that converts them.

    A column that refers to a concept, location or care site (or any row
    with a name) takes that row's name or its numeric id. Every other column
    is converted to its CDM type. YAML ``null`` sets the column to empty.
    """
    column = table.column(column_name)
    if yaml_value is None:
        row[column.name] = None
    elif refers_by_name(column):
        if column.refers_to is None:  # Never: refers_by_name checked this.
            raise AssertionError
        row[column.name] = id_for_name(row_names, column.refers_to, yaml_value)
        if is_concept_column(column.name):
            _fill_concept_source_columns(row, table, column.name, yaml_value)
    else:
        row[column.name] = yaml_to(column.type, yaml_value)
    return row


def check_omop_value(
    table: OmopTable, column_name: str, yaml_value: object, row_names: RowNames
) -> None:
    """Fail now, rather than while building tables, if a value can't fill a column."""
    set_omop_value(OmopRow(), table, column_name, yaml_value, row_names)


def _fill_concept_source_columns(
    row: OmopRow, table: OmopTable, concept_column: str, yaml_value: object
) -> None:
    """Also record a concept given by name in ``*_source_value``.

    Any concept also sets ``*_source_concept_id`` to unmapped (0), unless the
    scenario sets those columns itself.
    """
    stem = concept_column.removesuffix("_concept_id")
    source_value_column = f"{stem}_source_value"
    source_concept_column = f"{stem}_source_concept_id"
    given_by_name = not is_whole_number(yaml_value)
    if given_by_name and table.has_column(source_value_column):
        row.setdefault(source_value_column, str(yaml_value))
    if table.has_column(source_concept_column):
        row.setdefault(source_concept_column, 0)
