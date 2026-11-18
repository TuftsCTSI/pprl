"""Write every file that synth produces.

Dates are written ``yyyy-mm-dd`` and datetimes ``yyyy-mm-dd HH:MM:SS``.
Empty values are written as empty fields. An existing file is never
overwritten.
"""

import csv
from datetime import date, datetime
from typing import TYPE_CHECKING

from pprl._io import for_writing, write_linkages

if TYPE_CHECKING:
    from _csv import Writer
    from pathlib import Path
    from typing import IO, Literal

    from pprl.synth.site_records import SiteRecord

# The columns of the pprl patient file. No other column may be added, because
# pprl clean and pprl hash reject any column that is not one of their fields.
PATIENT_COLUMNS = ("person_id", "name", "city", "state", "zip", "dob")
# The columns of the true linkage. It uses the format of pprl link's output,
# with each site's person_id in place of the study_id that pprl clean would
# give it.
TRUE_LINKAGE_COLUMNS = ("group_id", "site", "person_id")


def _writer(
    handle: IO[str], quoting: Literal[0, 1, 2, 3, 4, 5] = csv.QUOTE_MINIMAL
) -> Writer:
    return csv.writer(handle, lineterminator="\n", quoting=quoting)


def format_value(value: object) -> str:
    """Format a value the way synth's CSV files hold it.

    >>> [format_value(v) for v in (None, 139.0, 7, date(2023, 1, 5))]
    ['', '139.0', '7', '2023-01-05']
    >>> format_value(datetime(2023, 1, 5))
    '2023-01-05 00:00:00'
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def write_omop_csvs(directory: Path, tables: list) -> None:
    """Write one CSV file per CDM table, named after the table, with every column."""
    directory.mkdir(parents=True)
    for table, rows in tables:
        with for_writing(directory / f"{table.name}.csv") as handle:
            writer = _writer(handle)
            writer.writerow(column.name for column in table.columns)
            for row in rows:
                writer.writerow(
                    format_value(_as_column_type(column.type, row.get(column.name)))
                    for column in table.columns
                )


def _as_column_type(column_type: type, value: object) -> object:
    if column_type is float and isinstance(value, int):
        return float(value)
    return value


def patient_row(record: SiteRecord) -> list:
    """Return the pprl patient file row for one site record."""
    pii = record.pii
    name = " ".join(part for part in (pii["first_name"], pii["last_name"]) if part)
    return [
        record.person_id,
        name,
        pii["city"] or "",
        pii["state"] or "",
        pii["zip"] or "",
        record.birth_date.isoformat(),
    ]


def write_patients_csv(path: Path, rows: list) -> None:
    """Write ``patients.csv``: one row per individual the site has a record of."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with for_writing(path) as handle:
        writer = _writer(handle)
        writer.writerow(PATIENT_COLUMNS)
        writer.writerows(rows)


def write_population_csv(
    path: Path, individuals: list, sites: list, person_ids: dict
) -> None:
    """Write ``population.csv``: each individual, and their person_id at each site.

    ``person_ids`` maps (individual_id, site) to the site's person_id. A site
    that has no record of the individual is left empty.
    """
    with for_writing(path) as handle:
        writer = _writer(handle)
        writer.writerow(["individual_id", "person_source_value", *sites])
        for individual in individuals:
            writer.writerow(
                [individual.individual_id, individual.person_source_value]
                + [
                    format_value(person_ids.get((individual.individual_id, site)))
                    for site in sites
                ]
            )


def write_true_linkages_csv(
    path: Path, individuals: list, sites: list, person_ids: dict
) -> None:
    """Write ``true_linkages.csv``: the linkage a perfect ``pprl link`` would find.

    There is one group per individual with records at two or more sites. The
    format is that of ``pprl link`` output, with the site's ``person_id`` in
    place of the study_id: ``group_id``, ``site`` and ``person_id``.
    """
    groups = [
        [site for site in sites if site in individual.sites]
        for individual in individuals
    ]
    linked = [
        (individual, recorded)
        for individual, recorded in zip(individuals, groups, strict=True)
        if len(recorded) > 1
    ]
    write_linkages(
        path,
        [
            (group_id, site, person_ids[(individual.individual_id, site)])
            for group_id, (individual, recorded) in enumerate(linked, start=1)
            for site in recorded
        ],
        TRUE_LINKAGE_COLUMNS,
    )


def write_expected_csv(path: Path, individuals: list) -> None:
    """Write ``expected.csv``: each ``expected`` result, one row per group and year.

    The file is not written when no individual has ``expected`` entries.
    """
    rows = [
        [
            individual.person_source_value,
            individual.description,
            group,
            str(year),
            _status_text(status),
        ]
        for individual in individuals
        for group, status_by_year in individual.expected.items()
        for year, status in status_by_year.items()
    ]
    if not rows:
        return
    with for_writing(path) as handle:
        writer = _writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(
            ["person_source_value", "description", "group", "year", "status"]
        )
        writer.writerows(rows)


def _status_text(status: object) -> str:
    if isinstance(status, (int, float)) and float(status).is_integer():
        return str(int(status))
    return str(status)
