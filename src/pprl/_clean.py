"""Run ``pprl clean``: prepare a site's raw patient file for hashing.

Every site runs clean on its raw patient file after pprl select, and then
hashes what clean writes. clean follows one fixed procedure, set by the
types of the fields, so every site prepares its data the same way:

* It silently drops every row whose id is not one of the study's patients.
  The study's patients are those in the rows that select kept from the
  site's tables. Only the study's patients are hashed. It warns about study
  patients who have no row in the file, because pack will leave their rows
  out.
* It rejects the file, writing nothing, if the file is not a well-formed
  table, or if it does not have exactly the
  columns the bundle names: the project's id column and the fields.
* It repairs, and reports each kind of repair: spaces around an id, date,
  ZIP or state value; a time after a date; a ZIP+4 code; and a state code
  not in capitals. It also converts a file saved as Windows text to UTF-8.
* It leaves names and other text fields exactly as they are. Names are
  expected to be messy, and hashing already ignores their case, accents,
  punctuation and spaces.
* It sets aside, and reports, each row it cannot repair without guessing:
  an empty or repeated id, a row with no identifying value, or a date, ZIP
  or state value it cannot read. The file of set-aside rows is the only
  file a site opens. For each row it holds the row number, id and reasons,
  and none of the row's identifying details.
* It replaces the site's id in every kept row with a study_id. The
  study_ids are a random shuffle of 1 to N, so nothing about the site's ids
  or the order of its export leaves the site. It writes one pprl file,
  which stays in the workspace. The file holds the rows in study_id order,
  and the study_id for each site id, for pack to use.

It ends with a warning for each kind of repair and each kind of set-aside
row, or with an all-clear. Messages identify rows by row number, never by
the site's id.
"""

import re
import secrets
from collections import defaultdict
from typing import TYPE_CHECKING

import pandas as pd

from . import _messages as msg
from . import _pprl_file, _wording
from ._dates import DATE, is_date
from ._inputs import (
    check_columns,
    check_csv,
    check_fields,
    check_id_column,
    check_input,
    check_output,
    check_patient_columns,
    check_pprl_input,
    unidentifiable,
)
from ._io import (
    CHUNK_ROWS,
    display_name,
    for_writing_together,
    spreadsheet_rows,
    write_csv_frame,
)
from ._messages import Kind, Label
from ._schema import FORMATS
from ._shared import STUDY_ID, Member
from ._ui import (
    ValidationContext,
    add_work,
    advance,
    finish_progress,
    note_user,
    ok_user,
    step,
    stoppable,
    warn_user,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ._pprl_file import PprlFile

# The steps of clean's progress bar, with each step's share of the bar.
STEPS = (
    (msg.STEP_READING_PATIENTS, 35),
    (msg.STEP_CLEANING, 37),
    (msg.STEP_WRITING_CLEANED, 28),
)

# ---------------------------------------------------------------------------
# Repairing one value
# ---------------------------------------------------------------------------

_DATE_AND_TIME = re.compile(
    rf"({DATE})[ T][0-9]{{2}}:[0-9]{{2}}(:[0-9]{{2}}(\.[0-9]+)?)?"
)
_ZIP_PLUS_4 = re.compile(r"([0-9]{5})-[0-9]{4}")


def _repair_date(value: str) -> tuple[str, list[str], str | None]:
    """Repair a date value.

    Return (repaired value, repairs made, reason it cannot be used).

    >>> _repair_date(" 1990-03-15 00:00:00")
    ('1990-03-15', ['spaces', 'time'], None)
    >>> _repair_date("03/15/1990")
    ('03/15/1990', [], 'date')
    """
    value, repairs = _without_spaces(value)
    match = _DATE_AND_TIME.fullmatch(value)
    if match and is_date(match.group(1)):
        return match.group(1), [*repairs, "time"], None
    if value == "" or is_date(value):
        return value, repairs, None
    return value, repairs, "date"


def _repair_zip(value: str) -> tuple[str, list[str], str | None]:
    """Repair a ZIP code value.

    Return (repaired value, repairs made, reason it cannot be used).

    A 4-digit code is set aside rather than given back a leading zero,
    because adding the zero would be a guess.

    >>> _repair_zip("02101-1234")
    ('02101', ['zip_plus_4'], None)
    >>> _repair_zip("2101")
    ('2101', [], 'zip_short')
    """
    value, repairs = _without_spaces(value)
    match = _ZIP_PLUS_4.fullmatch(value)
    if match:
        return match.group(1), [*repairs, "zip_plus_4"], None
    if value == "" or FORMATS["zip"](value):
        return value, repairs, None
    if re.fullmatch(r"[0-9]{4}", value):
        return value, repairs, "zip_short"
    return value, repairs, "zip"


def _repair_state(value: str) -> tuple[str, list[str], str | None]:
    """Repair a state value.

    Return (repaired value, repairs made, reason it cannot be used).

    A state name is set aside rather than converted to its code.

    >>> _repair_state(" ma")
    ('MA', ['spaces', 'state_case'], None)
    >>> _repair_state("Massachusetts")
    ('Massachusetts', [], 'state')
    """
    value, repairs = _without_spaces(value)
    if value == "" or FORMATS["state"](value):
        return value, repairs, None
    if re.fullmatch(r"[A-Za-z]{2}", value):
        return value.upper(), [*repairs, "state_case"], None
    return value, repairs, "state"


def _without_spaces(value: str) -> tuple[str, list[str]]:
    stripped = value.strip()
    return stripped, (["spaces"] if stripped != value else [])


_REPAIRERS = {"date": _repair_date, "zip": _repair_zip, "state": _repair_state}


# ---------------------------------------------------------------------------
# The clean command
# ---------------------------------------------------------------------------


def clean(
    patients: str | None = None,
    id_column: str | None = None,
    fields: dict | None = None,
    selected: str | None = None,
    output: str | None = None,
    *,
    flagged: str | None = None,
    provenance: dict,
    data_folder: Path | str,
    output_folder: Path | str,
) -> int:
    """Prepare a raw patient file for hashing (see the module docstring).

    Parameters
    ----------
    patients : str
        Filename of the raw patient CSV (in data_folder).
    id_column : str
        The name of the site's id column (the project's `id`).
    fields : dict
        The fields the file will be hashed with, and their types.
    selected : str
        Filename of the selected tables file pprl select wrote, which lists
        the study's patients (in data_folder). None keeps every row.
    output : str
        Output filename for the cleaned patient file, a pprl file. Its
        ``patients`` member holds the rows ready to hash, with their
        study_ids. Its ``study_ids`` member holds each kept row's site id
        and study_id.
    flagged : str
        Output filename for the rows set aside, each with its reason (CSV).
    provenance : dict
        The ``project``, bundle ``set`` and ``site`` the file is written for.

    Returns
    -------
    int
        0 on success (even with rows set aside), 1 on validation failure.
    """
    ctx = ValidationContext()
    path = check_input(
        ctx,
        Label.PATIENTS_FILE,
        "patients",
        Kind.PATIENTS,
        patients,
        folder=data_folder,
    )
    id_ok = check_id_column(ctx, id_column, fields)
    types = check_fields(ctx, fields)
    output_path = check_output(ctx, Label.OUTPUT_FILE, "output", output, output_folder)
    flagged_path = check_output(
        ctx, Label.FLAGGED_FILE, "flagged", flagged, output_folder
    )
    selected_file, study = None, None
    if selected is not None:
        selected_file = check_pprl_input(
            ctx,
            Label.SELECTED_FILE,
            selected,
            data_folder,
            "selected",
            provenance=provenance,
            site=provenance["site"],
            lazy=True,
        )
        if selected_file and id_ok and isinstance(id_column, str):
            study = _study(ctx, selected_file, id_column)
    df, converted = check_csv(
        ctx, Label.PATIENTS_CONTENT, path, need_rows=True, convert=True
    )
    required = (id_column,) if id_column is not None else ()  # if None, id_ok failed
    columns_ok = check_columns(ctx, Label.METADATA_COLUMNS, df, required, path)
    if id_ok and columns_ok and types and df is not None and id_column is not None:
        check_patient_columns(ctx, df, id_column, types, path)
    if ctx.conclude():
        return 1
    # Each of these is None only after a failure, which conclude has reported.
    if df is None or id_column is None or types is None or path is None:
        raise AssertionError
    if output_path is None or flagged_path is None:
        raise AssertionError

    not_selected = without = 0
    if study is not None:
        ids = df[id_column].str.strip()
        in_study = ids.isin(study)
        not_selected = int((~in_study).sum())
        without = len(study - set(ids[in_study]))
        df = df[in_study]

    stoppable("clean", len(df))
    step(msg.STEP_CLEANING)
    cleaned, repairs, reasons = _repaired(df, id_column, types)
    set_aside = pd.Series(
        [bool(row_reasons) for row_reasons in reasons], index=df.index
    )
    flagged_df = _set_aside_rows(df[set_aside], id_column, reasons)
    kept, mapping = _with_study_ids(cleaned[~set_aside], id_column)
    step(msg.STEP_WRITING_CLEANED)
    with for_writing_together(output_path, flagged_path):
        _pprl_file.write(
            output_path,
            "clean",
            {Member.PATIENTS: kept, Member.STUDY_IDS: mapping},
            provenance=provenance,
            inputs=[selected_file] if selected_file else [],
        )
        write_csv_frame(flagged_df, flagged_path)

    finish_progress()
    if not_selected:
        note_user(
            msg.CLEAN_NOT_SELECTED(
                count=not_selected, name=display_name(path), id=id_column
            )
        )
    if without:
        warn_user(msg.CLEAN_NO_PATIENT(count=without, name=display_name(path)))

    _report(
        names={
            "name": display_name(path),
            "output": display_name(output_path),
            "flagged": display_name(flagged_path),
            "id": id_column,
        },
        converted=converted,
        repairs=repairs,
        reasons=reasons,
        set_aside=set_aside,
        kept=len(df) - int(set_aside.sum()),
    )
    return 0


def _set_aside_rows(
    rows: pd.DataFrame, id_column: str, reasons: list[list[str]]
) -> pd.DataFrame:
    """Build the file of set-aside rows, for the site's data team.

    The team uses it to find and fix the rows in its own database. Each row
    gives its row number in the patient file, its id and the reasons. None
    of its identifying details are included.
    """
    return pd.DataFrame(
        {
            msg.ROW_COLUMN: [str(row) for row in spreadsheet_rows(rows.index)],
            id_column: rows[id_column].to_numpy(),
            msg.REASON_COLUMN: [
                _wording.shown_text(msg.REASON_SEPARATOR.join(r)) for r in reasons if r
            ],
        }
    )


def _study(
    ctx: ValidationContext, selected_file: PprlFile, id_column: str
) -> set[str] | None:
    """Return the ids of the study's patients from the selected file, as a set.

    Return None if the file does not list them.
    """
    member = selected_file.member(Member.COHORT)
    if member is None or member.get("columns") != [id_column]:
        ctx.fail(
            Label.SELECTED_FILE, msg.FILE_DAMAGED(name=display_name(selected_file.path))
        )
        return None
    study = set()
    with ctx.attempt(Label.SELECTED_FILE, _pprl_file.PprlFileError):
        for chunk in selected_file.chunks(Member.COHORT, CHUNK_ROWS):
            study.update(chunk[id_column])
        return study
    return None


def _with_study_ids(kept: pd.DataFrame, id_column: str) -> tuple[pd.DataFrame, ...]:
    """Replace the site's id in the kept rows with a study_id.

    Return two DataFrames, both in study_id order: the rows, and each site
    id with its study_id.

    The study_ids are a random shuffle of 1 to N, drawn from the operating
    system's randomness. So they reveal nothing about the site's ids, and the
    order of the rows reveals nothing about the order of the export.
    """
    shuffled = secrets.SystemRandom().sample(range(1, len(kept) + 1), len(kept))
    rows = kept.drop(columns=id_column)
    rows.insert(0, STUDY_ID, shuffled)
    mapping = pd.DataFrame({id_column: kept[id_column], STUDY_ID: shuffled})
    return tuple(
        df.sort_values(STUDY_ID).astype({STUDY_ID: str}).reset_index(drop=True)
        for df in (rows, mapping)
    )


def _repaired(
    df: pd.DataFrame, id_column: str, types: dict
) -> tuple[pd.DataFrame, dict, list[list[str]]]:
    """Repair what can be repaired, and record why each other row is set aside.

    Return the repaired DataFrame, {(column, repair): [index]}, and each
    row's reasons as message texts (empty for a kept row).
    """
    cleaned = df.copy()
    repairs = defaultdict(list)
    reasons = [[] for _ in range(len(df))]
    positions = {label: i for i, label in enumerate(df.index)}

    columns = [(id_column, None)]
    columns += [(c, t) for c, t in types.items() if t in _REPAIRERS]
    add_work(len(df) * (len(columns) + 1))
    for column, field_type in columns:
        repair = _REPAIRERS.get(field_type, lambda v: (*_without_spaces(v), None))
        values = []
        for label, value in df[column].items():
            new_value, made, reason = repair(value)
            values.append(new_value)
            for kind in made:
                repairs[(column, kind)].append(label)
            if reason:
                reasons[positions[label]].append(
                    msg.REASONS[reason](column=_wording.supplied(column))
                )
        cleaned[column] = values
        advance(len(df))

    ids = cleaned[id_column]
    counts = ids[ids != ""].value_counts()
    for label in df.index:
        site_id = ids[label]
        if site_id == "":
            reason = msg.REASONS["id_empty"](column=id_column)
            reasons[positions[label]].insert(0, reason)
        elif counts[site_id] > 1:
            reason = msg.REASONS["id_repeated"](column=id_column)
            reasons[positions[label]].insert(0, reason)
    for label in df.index[unidentifiable(cleaned, types)]:
        reasons[positions[label]].append(msg.REASONS["no_identifiers"]())
    advance(len(df))
    return cleaned, repairs, reasons


def _report(
    names: dict,
    *,
    converted: bool,
    repairs: dict,
    reasons: list[list[str]],
    set_aside: pd.Series,
    kept: int,
) -> None:
    """Show what clean repaired and set aside.

    A single warning covers everything that was set aside.
    """
    if converted:
        note_user(msg.CLEAN_CONVERTED(name=names["name"]))
    for (column, kind), found in repairs.items():
        labels = [label for label in found if not set_aside[label]]
        if labels:
            note_user(
                msg.CLEAN_REPAIRED(
                    column=_wording.supplied(column),
                    what=msg.REPAIRS[kind],
                    count=len(labels),
                    rows=spreadsheet_rows(labels),
                    name=names["name"],
                    output=names["output"],
                )
            )
    by_reason = defaultdict(list)
    for label, row_reasons in zip(set_aside.index, reasons, strict=True):
        for reason in row_reasons:
            by_reason[reason].append(label)
    for reason, labels in by_reason.items():
        note_user(
            msg.CLEAN_SET_ASIDE(
                count=len(labels),
                name=names["name"],
                reason=reason,
                rows=spreadsheet_rows(labels),
            )
        )
    if by_reason:
        warn_user(
            msg.CLEAN_SET_ASIDE_FILE(flagged=names["flagged"], name=names["name"])
        )
    ok_user(
        msg.WROTE_CLEANED(
            kept=kept,
            output=names["output"],
            id=names["id"],
            flagged=int(set_aside.sum()),
            flagged_name=names["flagged"],
        )
    )
