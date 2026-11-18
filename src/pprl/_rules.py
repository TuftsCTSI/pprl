"""Define the selection rules that decide which rows of each table are in the study.

Each table a site sends has an entry in the site's bundle. The entry gives
the table's columns, its primary key, and the rules that pprl select
applies to it, as the coordinator wrote them in project.yml:

    {"columns": ["person_id", "year", "neighborhood"],
     "primary_key": ["person_id", "year"],
     "keep": [{"not_empty": "neighborhood"},
              {"one_of": {"year": ["2023", "2024", "2025"]}}],
     "censor": []}

``keep`` lists the rules a row must meet to be kept. They are applied in
order, so select can report how many rows each rule left out. The kinds of
rule are:

* ``not_empty: <column>``: the column has a value. An empty cell is NULL.
* ``one_of: {<column>: [<value>, ...]}``: the column's value exactly
  matches one of these values, compared as text.
* ``found_in: {table: <table>, on: [<column>, ...]}``: the other table, as
  select kept it, has a row with the same values in these columns. The id
  column must be one of them.

``censor`` lists columns whose values select empties in every row. Each
table's primary key must be unique among the kept rows. pack checks every
rule again on what select wrote (see ``violations``).

Each kind of rule is a class, listed in ``KINDS``. The class says what can
be wrong with the rule's settings, which rows it keeps, how to describe a
row it leaves out (for select's summary), and how a guide shows it (for
pprl guides).

All rows of one patient share the patient's id, and ``found_in`` always
matches on the id. So a command can apply every rule to one subset of
patients at a time, using the rows of those patients from each table.
"""

from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING, override

import pandas as pd

from . import _messages as msg
from . import _wording
from ._messages import Label
from ._shared import is_text

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, TypeIs

    from ._io import Buckets
    from ._ui import ValidationContext

# The settings for each table in a site's bundle.
SETTINGS_KEYS = ("columns", "primary_key", "keep", "censor")


def parts(rule: dict) -> tuple[str, Any]:  # rule: a rule's settings as YAML gives them
    """Return a rule's kind and its settings.

    >>> parts({"not_empty": "neighborhood"})
    ('not_empty', 'neighborhood')
    """
    ((kind, setting),) = rule.items()
    return kind, setting


# ---------------------------------------------------------------------------
# The kinds of rule
# ---------------------------------------------------------------------------


class _Kind[S]:
    """A kind of rule, identified by the name used in ``keep``.

    The kind's wording is in RULE_PROBLEMS and RULE_DESCRIPTIONS.
    """

    name: str

    def problem(
        self, setting: object, name: str, tables: dict, id_column: str | None
    ) -> str | None:
        """Find what is wrong with ``setting``, from a rule of the table ``name``.

        Return the problem as the end of a RULE_INVALID message (see
        ``check_site_tables``), or None if nothing is wrong.
        """
        raise NotImplementedError

    def kept(self, setting: S, rows: pd.DataFrame, keys: Callable) -> pd.Series:
        """Return which ``rows`` meet the rule, as a boolean Series (see ``kept``)."""
        raise NotImplementedError

    def described(self, setting: S) -> str:
        """Describe a row that does not meet the rule."""
        raise NotImplementedError

    def rendered(
        self, setting: S, code: Callable[..., str], listed: Callable[..., str]
    ) -> str:
        """Describe what a row needs to meet the rule, for a guide."""
        raise NotImplementedError

    def looks_in(self, setting: S) -> list:  # noqa: ARG002 (subclasses may use it)
        """List the other tables the rule looks in."""
        return []


class _NotEmpty(_Kind[str]):
    """``not_empty: <column>``."""

    name = "not_empty"

    @override
    def problem(
        self, setting: object, name: str, tables: dict, id_column: str | None
    ) -> str | None:
        if not isinstance(setting, str):
            return msg.RULE_PROBLEMS["form"]()
        return _column_problem(setting, name, tables)

    @override
    def kept(self, setting: str, rows: pd.DataFrame, keys: Callable) -> pd.Series:
        return rows[setting] != ""

    @override
    def described(self, setting: str) -> str:
        return msg.RULE_DESCRIPTIONS[self.name](column=setting)

    @override
    def rendered(
        self, setting: str, code: Callable[..., str], listed: Callable[..., str]
    ) -> str:
        return f"{code(setting)} is not empty"


class _OneOf(_Kind[dict]):
    """``one_of: {<column>: [<value>, ...]}``."""

    name = "one_of"

    @override
    def problem(
        self, setting: object, name: str, tables: dict, id_column: str | None
    ) -> str | None:
        if not (
            isinstance(setting, dict)
            and len(setting) == 1
            and isinstance(values := next(iter(setting.values())), list)
            and values
            and all(isinstance(v, str) for v in values)
        ):
            return msg.RULE_PROBLEMS[self.name]()
        (column,) = setting
        return _column_problem(column, name, tables)

    @override
    def kept(self, setting: dict, rows: pd.DataFrame, keys: Callable) -> pd.Series:
        ((column, values),) = setting.items()
        return rows[column].isin(values)

    @override
    def described(self, setting: dict) -> str:
        ((column, values),) = setting.items()
        return msg.RULE_DESCRIPTIONS[self.name](column=column, count=len(values))

    @override
    def rendered(
        self, setting: dict, code: Callable[..., str], listed: Callable[..., str]
    ) -> str:
        ((column, values),) = setting.items()
        return f"{code(column)} is one of {listed(code(v) for v in values)}"


class _FoundIn(_Kind[dict]):
    """``found_in: {table: <table>, on: [<column>, ...]}``."""

    name = "found_in"

    @override
    def problem(
        self, setting: object, name: str, tables: dict, id_column: str | None
    ) -> str | None:
        problems = msg.RULE_PROBLEMS
        on = setting.get("on") if isinstance(setting, dict) else None
        if not (
            isinstance(setting, dict)
            and set(setting) == {"table", "on"}
            and isinstance(on, list)
            and on
            and all(isinstance(c, str) for c in on)
            and (id_column is None or id_column in on)
        ):
            return problems[self.name](id=id_column or "the id column")
        other = setting["table"]
        if other == name or other not in tables:
            return problems["table"](other=_wording.shown(other))
        for column in on:
            if problem := _column_problem(column, name, tables):
                return problem
            if column not in tables[other]["columns"]:
                return problems["on"](column=column, other=other)
        return None

    @override
    def kept(self, setting: dict, rows: pd.DataFrame, keys: Callable) -> pd.Series:
        on = setting["on"]
        found = _key_index(rows, on).isin(keys(setting["table"], on))
        return pd.Series(found, index=rows.index)

    @override
    def described(self, setting: dict) -> str:
        return msg.RULE_DESCRIPTIONS[self.name](
            table=setting["table"], columns=_wording.listed(setting["on"])
        )

    @override
    def rendered(
        self, setting: dict, code: Callable[..., str], listed: Callable[..., str]
    ) -> str:
        on = listed(code(c) for c in setting["on"])
        return f"{code(setting['table'])} has a row with the same {on}"

    @override
    def looks_in(self, setting: dict) -> list:
        return [setting["table"]]


# Every kind of rule, keyed by its name.
KINDS = {kind.name: kind for kind in (_NotEmpty(), _OneOf(), _FoundIn())}


def _column_problem(column: str, name: str, tables: dict) -> str | None:
    """Report a rule of the table ``name`` that names a column the table lacks.

    Return a message if ``column`` is not one of the table's columns, or None
    if it is.
    """
    if column in tables[name]["columns"]:
        return None
    return msg.RULE_PROBLEMS["column"](column=column, table=name)


# ---------------------------------------------------------------------------
# Checking the settings
# ---------------------------------------------------------------------------


def check_site_tables(
    ctx: ValidationContext,
    tables: object,
    id_column: str | None = None,
    label: str = Label.TABLES,
) -> bool:
    """Check `tables` as a site's bundle holds it, and return whether all are usable.

    The check covers each table's columns, primary key and rules (see the
    module docstring). If ``id_column`` is given, every table must have that
    column in its primary key. The checks are reported under ``label``.
    """
    if not _well_formed(tables):
        ctx.fail(label, msg.SITE_TABLES_INVALID())
        return False
    if id_column is not None:
        without = [
            name
            for name, settings in tables.items()
            if id_column not in settings["primary_key"]
        ]
        if without:
            ctx.fail(label, msg.SITE_TABLES_NO_ID(tables=without, column=id_column))
            return False
    problems = [
        problem
        for name in tables
        for problem in _table_problems(name, tables, id_column)
    ]
    loop = _loop(tables) if not problems else None
    if loop:
        problems.append(msg.RULES_CIRCULAR(tables=_wording.listed(loop)))
    for problem in problems:
        ctx.fail(label, problem)
    return not problems


def _well_formed(tables: object) -> TypeIs[dict]:
    """Tell whether each table has its columns and primary key as lists of names.

    The rules are checked separately, by ``_table_problems``.
    """

    def names(value: object) -> bool:
        return (
            isinstance(value, list) and bool(value) and all(is_text(c) for c in value)
        )

    return (
        isinstance(tables, dict)
        and bool(tables)
        and all(
            isinstance(settings, dict)
            and set(settings) == set(SETTINGS_KEYS)
            and names(settings["columns"])
            and names(settings["primary_key"])
            and set(settings["primary_key"]) <= set(settings["columns"])
            for settings in tables.values()
        )
    )


def _table_problems(name: str, tables: dict, id_column: str | None) -> list[str]:
    """List the problems with the rules of the table ``name``, as messages."""
    settings = tables[name]
    columns, keep, censor = settings["columns"], settings["keep"], settings["censor"]
    if not isinstance(keep, list):
        return [msg.KEEP_INVALID(table=name, value=_wording.shown(keep))]
    problems = []
    for n, rule in enumerate(keep, start=1):
        problem = _rule_problem(rule, name, tables, id_column)
        if problem:
            problems.append(msg.RULE_INVALID(n=n, table=name, problem=problem))
    if not (
        isinstance(censor, list)
        and all(c in columns and c not in settings["primary_key"] for c in censor)
    ):
        problems.append(msg.CENSOR_INVALID(table=name, value=_wording.shown(censor)))
    return problems


def _rule_problem(
    rule: object, name: str, tables: dict, id_column: str | None
) -> str | None:
    """Find what is wrong with one of a table's rules.

    Return the problem as the end of a RULE_INVALID message, or None if
    nothing is wrong.
    """
    if not (isinstance(rule, dict) and len(rule) == 1 and next(iter(rule)) in KINDS):
        return msg.RULE_PROBLEMS["form"]()
    kind, setting = parts(rule)
    return KINDS[kind].problem(setting, name, tables, id_column)


def _looks_in(settings: dict) -> list[str]:
    """List the tables that a table's ``found_in`` rules look in."""
    return [
        other
        for kind, setting in map(parts, settings["keep"])
        for other in KINDS[kind].looks_in(setting)
    ]


def _sorter(tables: dict) -> TopologicalSorter:
    """Sort the tables so each comes after the tables its ``found_in`` rules use."""
    return TopologicalSorter(
        {name: _looks_in(settings) for name, settings in tables.items()}
    )


def _loop(tables: dict) -> list[str] | None:
    """Find a loop of ``found_in`` rules and return its tables, or None."""
    try:
        _sorter(tables).prepare()
    except CycleError as error:
        return error.args[1][:-1]  # The cycle repeats its first table at the end.
    return None


def in_order(tables: dict) -> list:
    """Order the table names so each comes after the tables its rules look in.

    A table's ``found_in`` rules need those other tables to be selected first.

    >>> in_order({
    ...     "people": {"keep": [{"found_in": {"table": "years", "on": ["id"]}}]},
    ...     "years": {"keep": []},
    ... })
    ['years', 'people']
    """
    return list(_sorter(tables).static_order())


# ---------------------------------------------------------------------------
# Applying the rules
# ---------------------------------------------------------------------------


def kept(rule: dict, rows: pd.DataFrame, keys: Callable) -> pd.Series:
    """Tell which ``rows`` meet ``rule``, as a boolean Series.

    ``rows`` is a DataFrame of strings. ``keys(table, on)`` returns, as a
    MultiIndex, the values in the columns ``on`` from the rows that another
    table kept for the same patients.
    """
    kind, setting = parts(rule)
    return KINDS[kind].kept(setting, rows, keys)


def bucket_keys(buckets: dict[str, Buckets], b: int) -> Callable:
    """Make the ``keys`` function for ``kept`` and ``violations`` from bucket ``b``.

    ``buckets`` maps each table's name to its _io.Buckets. The function
    returns the rows that another table kept for the same patients, taken
    from bucket ``b``.
    """

    def keys(table: str, on: list[str]) -> pd.MultiIndex:
        return _key_index(buckets[table].read(b), on)

    return keys


def _key_index(rows: pd.DataFrame, on: list[str]) -> pd.MultiIndex:
    """Index the values of ``rows`` in the columns ``on``, in the form ``kept`` uses."""
    return pd.MultiIndex.from_frame(rows[on])


def violations(settings: dict, rows: pd.DataFrame, keys: Callable) -> int:
    """Count the ``rows`` that a table's settings would not have let select write.

    Such a row fails a rule, has a value in a censored column, or shares its
    primary key with another row.
    """
    bad = pd.Series(data=False, index=rows.index)
    for rule in settings["keep"]:
        bad |= ~kept(rule, rows, keys)
    for column in settings["censor"]:
        bad |= rows[column] != ""
    bad |= rows.duplicated(settings["primary_key"], keep=False)
    return int(bad.sum())


def described(rule: dict) -> str:
    """Describe a row that does not meet ``rule``, for select's summary."""
    kind, setting = parts(rule)
    return KINDS[kind].described(setting)


def rendered(rule: dict, code: Callable[..., str], listed: Callable[..., str]) -> str:
    """Describe what a row must have to meet ``rule``, for a guide.

    ``code`` formats a column, table or value, and ``listed`` joins the
    formatted values into a phrase.

    >>> rendered({"one_of": {"year": ["2024", "2025"]}}, repr, " or ".join)
    "'year' is one of '2024' or '2025'"
    """
    kind, setting = parts(rule)
    return KINDS[kind].rendered(setting, code, listed)
