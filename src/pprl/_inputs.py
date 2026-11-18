"""Check a command's inputs, recording each problem as a row of a table.

Every command checks all of its inputs before it writes anything. The
inputs are files and their names, file contents, and settings. Each check
returns what it checked (a path, a DataFrame or a value), or None if the
check failed. The wording comes from the message catalog.
"""

import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from clkhash.serialization import deserialize_bitarray

from . import _messages as msg
from . import _pprl_file, _system, _wording
from ._io import (
    display_name,
    read_csv_converting,
    spreadsheet_rows,
    validated_file_path,
    validated_out_path,
    validated_site,
)
from ._messages import Label
from ._schema import (
    FILL_RATIO_MAX,
    FORMATS,
    HASH_LENGTH_MAX,
    HASH_LENGTH_MIN,
    PREPARERS,
    field_types,
)
from ._shared import (
    STUDY_ID,
    Member,
    is_number,
    is_text,
    is_whole_number,
    repeated,
)
from ._ui import ValidationContext, info_log

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ._pprl_file import PprlFile

# ---------------------------------------------------------------------------
# Files and their names
# ---------------------------------------------------------------------------


def _checked_path(
    ctx: ValidationContext,
    label: str,
    find: Callable[[], Path],
    problems: tuple[type[Exception], ...],
) -> Path | None:
    """Return the path that ``find()`` returns, or record its problem and return None.

    If ``find()`` raises one of ``problems``, the recorded row holds that
    error's message.
    """
    with ctx.attempt(label, problems):
        return find()
    return None


def check_input(
    ctx: ValidationContext,
    label: str,
    key: str,
    kind: str,
    name: str | None,
    *,
    folder: Path | str,
    supplied: bool = False,
) -> Path | None:
    """Check that the input file ``name`` (from setting ``key``) exists.

    ``supplied`` means the user named the file, so every message marks the
    name that way, so that the log withholds it.
    """
    return _checked_path(
        ctx,
        label,
        lambda: validated_file_path(key, kind, name, folder, supplied=supplied),
        (FileNotFoundError, TypeError, ValueError),
    )


def check_output(
    ctx: ValidationContext,
    label: str,
    key: str,
    name: str | None,
    folder: Path | str,
) -> Path | None:
    """Check that the output ``name`` (from setting ``key``) does not exist yet.

    The output may be a file or a folder.
    """
    return _checked_path(
        ctx,
        label,
        lambda: validated_out_path(key, name, folder),
        (FileExistsError, TypeError, ValueError),
    )


# How many bytes at the start of a file are examined to see how it was saved.
_SAMPLED = 64 * 1024


def _endings(data: bytes) -> str:
    r"""Name the line endings used at the start of a file.

    The result is Windows, Unix, classic Mac, mixed or none.

    >>> _endings(b"a,b\r\n1,2\r\n"), _endings(b"a\nb\r\n"), _endings(b"a,b")
    ('\r\n', 'mixed', 'none')
    """
    windows = data.count(b"\r\n")
    unix = data.count(b"\n") - windows
    mac = data.count(b"\r") - windows
    found = [e for e, n in (("\r\n", windows), ("\n", unix), ("\r", mac)) if n]
    return found[0] if len(found) == 1 else ("mixed" if found else "none")


def log_csv(
    path: Path,
    rows: int,
    columns: int,
    encoding: str | None = None,
    *,
    supplied: bool = False,
) -> None:
    """Log how a CSV file that a command read was saved, and its shape.

    Never log its contents or its column names, which the user supplied.
    Also leave out its name if the user ``supplied`` it.
    """
    with Path(path).open("rb") as handle:
        start = handle.read(_SAMPLED)
    if encoding is None:
        encoding = "utf-8-sig" if start.startswith(b"\xef\xbb\xbf") else "utf-8"
    info_log(
        msg.INFO_INPUT_CSV(
            name=display_name(path, supplied=supplied),
            size=_wording.size(path.stat().st_size),
            rows=rows,
            columns=columns,
            encoding=msg.ENCODINGS[encoding],
            endings=msg.LINE_ENDINGS[_endings(start)],
        )
    )


def log_pprl_file(path: Path, pprl_file: PprlFile) -> None:
    """Log a pprl file that a command read.

    Log the header's kind, release and random ids, the members' shapes, and
    the files it was made from.
    """
    header = pprl_file.header
    members = _wording.listed(
        msg.INFO_MEMBER(
            name=m["name"], rows=m.get("rows", 0), columns=len(m.get("columns", ()))
        )
        for m in header["members"]
    )
    inputs = _wording.listed(
        msg.INFO_FROM_FILE(kind=entry["kind"], id=entry["id"])
        for entry in header["inputs"]
    )
    info_log(
        msg.INFO_INPUT_PPRL(
            name=_file_name(path),
            size=_wording.size(path.stat().st_size),
            kind=header["kind"],
            release=header["release"],
            id=header["id"],
            set=header["set"],
            members=members,
            inputs=msg.INFO_FROM(files=inputs) if inputs else "",
        )
    )


def check_csv(
    ctx: ValidationContext,
    label: str,
    path: Path | None,
    *,
    need_rows: bool = False,
    supplied: bool = False,
    convert: bool = False,
) -> tuple[pd.DataFrame | None, bool]:
    """Read the CSV file at ``path`` (from check_input), recording the result in ctx.

    Return the DataFrame, or None. Also return whether the file was converted
    from Windows text, which can only happen with ``convert`` (see
    read_csv_converting).

    With ``need_rows``, a file with a header but no rows fails. ``supplied``
    means the user named the file (see check_input).
    """
    if path is None:
        return None, False
    with ctx.attempt(label, ValueError) as attempt:
        df, converted = read_csv_converting(path, convert=convert, supplied=supplied)
    if attempt.failed:
        return None, False
    encoding = "cp1252" if converted else None
    log_csv(path, len(df), len(df.columns), encoding, supplied=supplied)
    if need_rows and len(df) == 0:
        ctx.fail(label, msg.FILE_NO_ROWS(name=display_name(path, supplied=supplied)))
        return None, False
    return df, converted


def check_input_csv(
    ctx: ValidationContext,
    labels: tuple[str, str],
    key: str,
    kind: str,
    name: str | None,
    *,
    folder: Path | str,
    need_rows: bool = False,
    supplied: bool = False,
) -> tuple[Path | None, pd.DataFrame | None]:
    """Run check_input, then check_csv. ``labels`` are for the file and its contents."""
    file_label, content_label = labels
    path = check_input(
        ctx, file_label, key, kind, name, folder=folder, supplied=supplied
    )
    df, _ = check_csv(ctx, content_label, path, need_rows=need_rows, supplied=supplied)
    return path, df


# ---------------------------------------------------------------------------
# File contents
# ---------------------------------------------------------------------------


def check_site(ctx: ValidationContext, site: str) -> str | None:
    """Check a site name from the bundle. Return the site, or None."""
    with ctx.attempt(Label.SITE, ValueError):
        validated_site(site)
        return site
    return None


def check_sites(
    ctx: ValidationContext,
    sites: object,
    not_list: _wording.Message,
    at_least: int = 1,
    reserved: Iterable[str] = (),
) -> bool:
    """Check `sites`, and return whether it is usable.

    `sites` must be a list of at least ``at_least`` site names; ``not_list``
    is the message if it is not a list. Each name must be usable and listed
    once. No name may be one of the ``reserved`` names, in any mix of upper
    and lower case, because Windows and macOS drives treat case-only
    differences as the same file name.
    """
    if not (isinstance(sites, list) and len(sites) >= at_least):
        ctx.fail(Label.SITES, not_list(value=_wording.shown(sites)))
        return False
    with ctx.attempt(Label.SITES, ValueError) as attempt:
        for site in sites:
            validated_site(site)
    if attempt.failed:
        return False
    if twice := repeated(sites):
        ctx.fail(Label.SITES, msg.SITES_REPEATED(sites=_wording.listed(twice)))
        return False
    taken = [site for site in sites if site.lower() in reserved]
    if taken:
        ctx.fail(
            Label.SITES,
            msg.SITES_RESERVED(
                sites=_wording.listed(taken), names=_wording.listed(reserved)
            ),
        )
        return False
    return True


def check_columns(
    ctx: ValidationContext,
    label: str,
    df: pd.DataFrame | None,
    required: tuple[str, ...],
    path: Path | str | None,
) -> bool:
    """Check that every required column exists in df.

    Return True if all are present, and False otherwise.
    """
    if df is None:
        return False
    missing = [c for c in required if c not in df.columns]
    if missing:
        ctx.fail(label, _missing_columns(df, missing, path))
        return False
    return True


def _file_name(path: Path | str | None) -> str:
    """Return the name of the file whose rows are checked (see display_name)."""
    if path is None:  # Never: a file's rows always come with its name.
        raise AssertionError
    return display_name(path)


def _missing_columns(
    df: pd.DataFrame, missing: Iterable[str], path: Path | str | None
) -> str:
    """Return the message for missing columns. It also lists the file's columns."""
    return msg.MISSING_COLUMNS(
        name=_file_name(path),
        columns=[_wording.supplied(column) for column in missing],
        found=_wording.supplied_list(df.columns),
    )


def check_ids(
    ctx: ValidationContext,
    label: str,
    df: pd.DataFrame,
    path: Path | str | None,
    column: str = STUDY_ID,
) -> bool:
    """Check that every id in ``column`` has no surrounding spaces and is unique.

    Each offending row is listed by its row number, never by its id.
    """
    ok = True
    ids = df[column]
    badly_written = (ids.str.strip() == "") | (ids != ids.str.strip())
    if badly_written.any():
        ctx.fail(
            label,
            msg.IDS_INVALID(
                column=column,
                name=_file_name(path),
                count=int(badly_written.sum()),
                rows=spreadsheet_rows(df.index[badly_written]),
            ),
        )
        ok = False
    counts = ids[~badly_written].value_counts()
    repeated = counts[counts > 1].index
    if len(repeated):
        ctx.fail(
            label,
            msg.DUPLICATE_IDS(
                column=column,
                name=_file_name(path),
                count=len(repeated),
                rows=spreadsheet_rows(df.index[ids.isin(repeated) & ~badly_written]),
            ),
        )
        ok = False
    return ok


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def check_number_in_range(
    ctx: ValidationContext,
    label: str,
    message: _wording.Message,
    value: object,
    lo: float = 0.0,
    *,
    hi: float = 1.0,
) -> bool:
    """Check that value is a number within [lo, hi], or fail with ``message``.

    Return True on success and False on failure.
    """
    if not is_number(value) or not (lo <= value <= hi):
        ctx.fail(label, message(value=_wording.shown(value)))
        return False
    return True


def check_true_false(ctx: ValidationContext, config: dict, key: str) -> bool | None:
    """Read a true/false setting, which defaults to false, under the label ``key``.

    Return None, after recording a failure, for any other value.
    """
    value = config.get(key, False)
    if not isinstance(value, bool):
        ctx.fail(key, msg.CONFIG_NOT_TRUE_FALSE(key=key, value=_wording.shown(value)))
        return None
    return value


def check_workers(ctx: ValidationContext, workers: object) -> int:
    """Check the number of worker processes.

    It must be a positive whole number, or absent, meaning all cores.
    """
    if workers is None:
        return _system.cpus()
    if is_whole_number(workers) and workers > 0:
        return workers
    ctx.fail(Label.WORKERS, msg.WORKERS_INVALID(value=_wording.shown(workers)))
    return 1


# If hashes leak, the secret is the only thing that stops someone from hashing
# guessed values and comparing. It is 32 random bytes, written in hexadecimal.
SECRET_LENGTH = 64


def new_secret() -> str:
    """Make a new random secret for pprl secrets and pprl sweep.

    >>> len(new_secret()) == SECRET_LENGTH
    True
    """
    return secrets.token_hex(SECRET_LENGTH // 2)


# ---------------------------------------------------------------------------
# Patient files, their fields, and the hashing settings
# ---------------------------------------------------------------------------


def check_fields(ctx: ValidationContext, fields: object) -> dict | None:
    """Check the fields to hash. Return each field's type, or None."""
    if fields is None:
        ctx.fail(Label.FIELDS, msg.FIELDS_MISSING())
        return None
    with ctx.attempt(Label.FIELDS, ValueError):
        return field_types(fields)
    return None


def check_hash_length(ctx: ValidationContext, value: object, fields: object) -> None:
    """Check a project's or sweep's `hash_length`, the number of bits in each hash.

    It must be a whole number, a multiple of 8, within the limits in
    ``_schema``, and leave at least 8 bits for each field. ``fields`` itself
    may be unusable; check_fields reports that.
    """
    count = len(fields) if isinstance(fields, dict) else 0
    if not (
        is_whole_number(value)
        and value % 8 == 0
        and max(HASH_LENGTH_MIN, 8 * count) <= value <= HASH_LENGTH_MAX
    ):
        ctx.fail(
            Label.HASH_LENGTH_SETTING,
            msg.HASH_LENGTH_INVALID(
                value=_wording.supplied(_wording.shown(value)),
                lo=HASH_LENGTH_MIN,
                hi=HASH_LENGTH_MAX,
            ),
        )


def check_fill_ratio(ctx: ValidationContext, value: object) -> None:
    """Check a project's or sweep's `fill_ratio`: a number above 0, up to the limit."""
    if not (is_number(value) and 0 < value <= FILL_RATIO_MAX):
        ctx.fail(
            Label.FILL_RATIO,
            msg.FILL_RATIO_INVALID(
                value=_wording.supplied(_wording.shown(value)), hi=FILL_RATIO_MAX
            ),
        )


def check_xor_folds(ctx: ValidationContext, value: object) -> None:
    """Check a project's or sweep's `xor_folds`, which must be a whole number >= 0.

    Each fold doubles the number of bits hashed into, so a very high value
    would run out of memory.
    """
    if type(value) is not int or value < 0:
        ctx.fail(
            Label.XOR_FOLDS,
            msg.XOR_FOLDS_INVALID(value=_wording.supplied(_wording.shown(value))),
        )


def check_project_salt(ctx: ValidationContext, value: object) -> None:
    """Check a project's or sweep's `project_salt`, which must be text if given."""
    if value is not None and not is_text(value):
        ctx.fail(
            Label.PROJECT_SALT,
            msg.PROJECT_SALT_INVALID(value=_wording.supplied(_wording.shown(value))),
        )


def check_record_salt(ctx: ValidationContext, value: object, fields: object) -> None:
    """Check a project's or sweep's `record_salt`, if it has one.

    Its ``field`` must be one of ``fields``, and its ``length`` must be valid
    if given. ``fields`` itself may be unusable; check_fields reports that.
    """
    if value is None:
        return
    well_formed = (
        isinstance(value, dict)
        and "field" in value
        and value.keys() <= {"field", "length"}
        and is_whole_number(value.get("length", 1))
        and value.get("length", 1) >= 1
    )
    if not well_formed:
        ctx.fail(
            Label.RECORD_SALT,
            msg.RECORD_SALT_INVALID(value=_wording.supplied(_wording.shown(value))),
        )
    elif isinstance(fields, dict) and value["field"] not in fields:
        ctx.fail(
            Label.RECORD_SALT,
            msg.RECORD_SALT_NOT_A_FIELD(
                value=_wording.supplied(_wording.shown(value["field"]))
            ),
        )


# Column names that pprl uses in its own files. A site's id column may not use
# them.
RESERVED_COLUMNS = frozenset({STUDY_ID, "group_id", "site", "hash"})


def check_id_column(
    ctx: ValidationContext, id_column: object, fields: object = None
) -> bool:
    """Check the project's `id`, the column that holds a site's own id.

    It may not be a column name that pprl uses, and may not be a field.
    """
    if id_column is None:
        ctx.fail(Label.ID_COLUMN, msg.ID_COLUMN_MISSING())
        return False
    if (
        not isinstance(id_column, str)
        or not id_column
        or id_column != id_column.strip()
    ):
        ctx.fail(
            Label.ID_COLUMN, msg.ID_COLUMN_INVALID(value=_wording.shown(id_column))
        )
        return False
    if id_column in RESERVED_COLUMNS:
        ctx.fail(Label.ID_COLUMN, msg.ID_COLUMN_RESERVED(value=id_column))
        return False
    if isinstance(fields, dict) and id_column in fields:
        ctx.fail(
            Label.ID_COLUMN,
            msg.ID_COLUMN_IS_FIELD(value=_wording.supplied(id_column)),
        )
        return False
    return True


def check_formats(
    ctx: ValidationContext, df: pd.DataFrame, types: dict, name: str | None
) -> None:
    """Check every date, zip and state field against its format, as written.

    Spaces around a value, or a lowercase state, count as errors.

    The message lists rows by their row number, never by a value or id.
    """
    for column, field_type in types.items():
        if field_type not in FORMATS:
            continue
        has_format = FORMATS[field_type]
        bad = df[(df[column] != "") & ~df[column].map(has_format).astype(bool)]
        if len(bad):
            description, how = msg.FORMATS[field_type]
            ctx.fail(
                Label.FORMAT(column=_wording.supplied(column)),
                msg.BAD_FORMAT(
                    count=len(bad),
                    column=_wording.supplied(column),
                    name=name,
                    format=description,
                    rows=spreadsheet_rows(bad.index),
                    how=how,
                ),
            )


def unidentifiable(df: pd.DataFrame, types: dict) -> pd.Series:
    """Find the rows that have no identifying value.

    Such a row has every field empty once prepared for hashing. So a field
    containing only spaces or punctuation counts as empty.
    """
    empty = pd.Series(data=True, index=df.index)
    for column, field_type in types.items():
        empty &= df[column].map(PREPARERS[field_type]) == ""
    return empty


def check_exact_columns(
    ctx: ValidationContext,
    label: str,
    df: pd.DataFrame,
    columns: list[str],
    path: Path | str | None,
) -> bool:
    """Check that a file has exactly ``columns``, in any order."""
    name = _file_name(path)
    missing = [c for c in columns if c not in df.columns]
    unexpected = [c for c in df.columns if c not in columns]
    if missing:
        ctx.fail(label, _missing_columns(df, missing, path))
    if unexpected:
        ctx.fail(
            label,
            msg.UNEXPECTED_COLUMNS(
                name=name, columns=[_wording.supplied(c) for c in unexpected]
            ),
        )
    return not (missing or unexpected)


def check_patient_columns(
    ctx: ValidationContext,
    df: pd.DataFrame,
    id_column: str,
    types: dict,
    path: Path | str | None,
    *,
    ignore_other_columns: bool = False,
) -> bool:
    """Check that a patient file has exactly ``id_column`` and the fields.

    With ``ignore_other_columns``, other columns are allowed too.
    """
    if ignore_other_columns:
        df = df[[c for c in df.columns if c in {id_column, *types}]]
    return check_exact_columns(ctx, Label.COLUMNS, df, [id_column, *types], path)


def check_patients(
    ctx: ValidationContext,
    df: pd.DataFrame | None,
    name: str | None,
    fields: object,
    *,
    ignore_other_columns: bool = False,
    id_column: str = STUDY_ID,
) -> pd.DataFrame | None:
    """Check a cleaned patient file's rows and the fields to hash, as hash does.

    ``df`` is None if the file could not be read.

    The rows must have exactly the columns study_id and the fields. Any other
    column is an error, so that a file that doesn't match the config stops
    the run. ``ignore_other_columns`` allows other columns, and
    ``id_column`` allows an id column other than study_id. These are for
    callers that never write the rows anywhere, such as a grid sweep on
    synthetic patient files.

    Return the rows ready to hash, or None if anything failed.
    """
    types = check_fields(ctx, fields)
    if df is not None and len(df) == 0:
        ctx.fail(Label.PATIENTS_CONTENT, msg.FILE_NO_ROWS(name=name))
        df = None

    metadata_ready = check_columns(ctx, Label.METADATA_COLUMNS, df, (id_column,), name)
    if metadata_ready:
        if df is None:  # Never: check_columns found the columns.
            raise AssertionError
        check_ids(ctx, Label.IDS, df, name, id_column)

    if not (metadata_ready and types):
        return None
    if df is None:  # Never: check_columns found the columns.
        raise AssertionError
    if not check_patient_columns(
        ctx, df, id_column, types, name, ignore_other_columns=ignore_other_columns
    ):
        return None

    check_formats(ctx, df, types, name)
    empty = unidentifiable(df, types)
    if empty.any():
        ctx.fail(
            Label.IDENTIFIERS,
            msg.NO_IDENTIFIERS(
                name=name,
                count=int(empty.sum()),
                rows=spreadsheet_rows(df.index[empty]),
            ),
        )
    if ctx.has_failures:
        return None

    rows = df[[id_column]].rename(columns={id_column: STUDY_ID})
    for column, field_type in types.items():
        rows[column] = df[column].map(PREPARERS[field_type])
    return rows


# ---------------------------------------------------------------------------
# pprl files
# ---------------------------------------------------------------------------


def check_pprl_input(
    ctx: ValidationContext,
    label: str,
    name: object,
    folder: Path | str,
    kind: str,
    *,
    provenance: dict | None = None,
    site: str | None = None,
    lazy: bool = False,
) -> PprlFile | None:
    """Check the pprl file ``name`` in ``folder``.

    Check that the file exists, is complete, and is a file of ``kind`` for
    this release. With ``provenance`` (a dict of the workspace's ``project``
    and bundle ``set``), also check that the file belongs to this project
    and bundle set, and to ``site`` if given. Return the PprlFile, or None.
    With ``lazy``, the members are not loaded.
    """
    provenance = provenance or {}
    problems = (FileNotFoundError, TypeError, ValueError, _pprl_file.PprlFileError)
    with ctx.attempt(label, problems) as attempt:
        path = validated_file_path(kind, msg.FILE_KINDS[kind], name, folder)
        pprl_file = _pprl_file.read(
            path,
            kind,
            project=provenance.get("project"),
            set_id=provenance.get("set"),
            site=site,
            lazy=lazy,
        )
    if attempt.failed:
        return None
    log_pprl_file(path, pprl_file)
    return pprl_file


# ---------------------------------------------------------------------------
# Hash files
# ---------------------------------------------------------------------------


@dataclass
class HashFile:
    """A hash file that has been checked (see check_hash_files).

    It holds the file, its site, its rows, and their hashes as bitarrays.
    """

    file: _pprl_file.PprlFile
    site: str
    rows: pd.DataFrame
    hashes: list = field(default_factory=list)


def check_hash_files(
    ctx: ValidationContext,
    hashes: object,
    data_folder: Path | str,
    sites: list[str] | None = None,
    provenance: dict | None = None,
) -> list[HashFile | None]:
    """Check the hash files for link or sweep, recording results in ctx.

    Check that there are at least 2 files, and that each is a complete hash
    file with rows (see check_pprl_input). Check that their sites differ and
    that their hashes have the same length. Each file's first hash stands
    for the whole file: hash writes all hashes at one length, and
    check_pprl_input has confirmed the file is complete as hash wrote it.
    With ``sites``, the i-th file must belong to the i-th site.

    Return a HashFile for each file, in order, with None for a file that
    failed.
    """
    names: list = []
    if isinstance(hashes, list) and len(hashes) > 1:
        names = hashes
    else:
        ctx.fail(Label.HASH_FILES, msg.HASHES_NOT_LIST(value=_wording.shown(hashes)))

    n_files = len(names)
    found: list[HashFile | None] = [None] * n_files
    for i in range(n_files):
        n = i + 1
        pprl_file = check_pprl_input(
            ctx,
            Label.HASH_FILE(n=n),
            names[i],
            data_folder,
            "hashes",
            provenance=provenance,
            site=sites[i] if sites else None,
        )
        if pprl_file is None:
            continue
        df = pprl_file.members[Member.HASHES]
        if len(df) == 0:
            ctx.fail(Label.HASH_RECORDS(n=n), msg.FILE_NO_ROWS(name=names[i]))
            continue
        found[i] = HashFile(pprl_file, pprl_file.header["site"], df)

    known_sites = [record.site for record in found if record is not None]
    if duplicates := repeated(known_sites):
        ctx.fail(
            Label.SITE_PAIRING,
            msg.DUPLICATE_SITES(sites=_wording.listed(duplicates)),
        )

    _check_hashes(ctx, names, found)
    return found


def checked_hash_files(found: list[HashFile | None]) -> list[HashFile]:
    """Return the hash files that check_hash_files found, all of them usable.

    Call this only after the checks have concluded without a failure.
    """
    usable = [hash_file for hash_file in found if hash_file is not None]
    if len(usable) != len(found):  # Never: a failure would have stopped the run.
        raise AssertionError
    return usable


def _check_hashes(
    ctx: ValidationContext, hashes: list, found: list[HashFile | None]
) -> None:
    """Read each file's hashes, and check that they all have the same length.

    ``found`` comes from check_hash_files. A file whose hashes can't be read
    is recorded as failed and set to None.
    """
    bit_lengths = []
    for i, record in enumerate(found):
        if record is None:
            continue
        try:
            record.hashes = list(map(deserialize_bitarray, record.rows["hash"]))
        except ValueError:
            ctx.fail(Label.HASH_VALUES(n=i + 1), msg.HASHES_UNREADABLE(name=hashes[i]))
            found[i] = None
            continue
        bit_lengths.append((i, len(record.hashes[0])))
    if len(bit_lengths) > 1:
        first_bits = bit_lengths[0][1]
        if any(bits != first_bits for _, bits in bit_lengths):
            details = _wording.listed(
                msg.HASH_BITS(name=hashes[i], bits=bits) for i, bits in bit_lengths
            )
            ctx.fail(Label.HASH_LENGTH, msg.HASH_LENGTH_MISMATCH(details=details))
