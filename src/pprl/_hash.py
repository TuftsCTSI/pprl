"""Run ``pprl hash``: turn a site's cleaned patients into Bloom filter hashes (CLKs).

The command checks all of its inputs before it writes any output. It reads
input files from ``data_folder`` and writes output to ``output_folder``.
The CLI passes the workspace for both.
"""

from itertools import islice, repeat
from typing import TYPE_CHECKING

from clkhash.clk import hash_chunk
from clkhash.key_derivation import generate_key_lists
from clkhash.schema import from_json_dict
from clkhash.serialization import serialize_bitarray
from clkhash.validate_data import validate_header

from . import _locking, _pprl_file, _system
from . import _messages as msg
from ._inputs import (
    check_fill_ratio,
    check_hash_length,
    check_output,
    check_patients,
    check_pprl_input,
    check_project_salt,
    check_record_salt,
    check_site,
    check_xor_folds,
)
from ._io import display_name
from ._messages import Label
from ._pprl_file import PprlFileError
from ._schema import expand_schema, record_salt_values
from ._shared import HASH_COLUMNS, RECORD_COLUMNS, Member
from ._ui import ValidationContext, advance, finish_progress, ok_user, step, stoppable
from ._workers import worker_pool

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    import pandas as pd
    from clkhash.schema import Schema

# Records are hashed in chunks of HASH_CHUNK_ROWS, the way clkhash itself does.
# When there is more than one chunk, the chunks run in worker processes. The
# result is the same either way.
HASH_CHUNK_ROWS = 10_000

# The steps of hash's progress bar, with each step's share of the bar.
STEPS = (
    (msg.STEP_READING_CLEANED, 15),
    (msg.STEP_HASHING, 63),
    (msg.STEP_WRITING_HASHES, 22),
)


def _salted_records(
    rows: pd.DataFrame, schema: Schema, record_salt: dict | None
) -> dict:
    """Group the records in ``rows`` by the HKDF salt each one uses.

    Each group gives the salt, the records' positions, and the records.
    Without a ``record_salt``, all records share the schema's own salt. With
    one, each record's salt is the schema's salt followed by the record's own
    salt value.
    """
    records = list(rows.itertuples(name=None))
    if record_salt is None:
        return {schema.kdf_salt: (range(len(records)), records)}
    groups = {}
    for place, (record, value) in enumerate(
        zip(records, record_salt_values(rows, record_salt), strict=True)
    ):
        salt = (schema.kdf_salt or b"") + b"\0" + value.encode("utf-8")
        places, salted = groups.setdefault(salt, ([], []))
        places.append(place)
        salted.append(record)
    return groups


def _hash_rows(
    rows: pd.DataFrame,
    secret: str,
    schema: Schema,
    workers: int | None = None,
    chunk_rows: int = HASH_CHUNK_ROWS,
    *,
    record_salt: dict | None = None,
) -> list:
    """Hash each row of ``rows``, in order.

    The hashes are the same as clkhash's generate_clk_from_csv would make
    from the rows written as CSV. The difference is that the work runs in
    worker processes that leave Ctrl+C to pprl.

    ``rows`` is a DataFrame of the record columns and the fields, as
    ``check_patients`` returns it. Every field's value is a string. Each
    record passed to clkhash is the row's index followed by its values, to
    match the schema's features. The schema's first feature is unnamed, and
    clkhash ignores it and the record columns (see ``expand_schema``).

    With a ``record_salt``, records with different salt values get different
    keys, so the same bigram sets different bits in them (see
    ``record_salt_values``).
    """
    validate_header(schema.fields, ["", *rows.columns])
    order, chunks, keys = [], [], []
    for salt, (places, salted) in _salted_records(rows, schema, record_salt).items():
        salted_keys = generate_key_lists(
            secret,
            len(schema.fields),
            key_size=schema.kdf_key_size,
            salt=salt,
            info=schema.kdf_info,
            kdf=schema.kdf_type,
            hash_algo=schema.kdf_hash,
        )
        order.extend(places)
        records = iter(salted)
        while chunk := list(islice(records, chunk_rows)):
            chunks.append(chunk)
            keys.append(salted_keys)
    offsets = [i * chunk_rows for i in range(len(chunks))]
    arguments = (chunks, keys, repeat(schema), repeat(True), offsets)  # noqa: FBT003 (a pool maps them)
    workers = min(workers or _system.cpus(), len(chunks))
    # zip is not strict here: the repeat() iterators never end, so the chunks
    # decide the length.
    if workers <= 1:
        hashed = _collected(map(hash_chunk, *arguments, strict=False))
    else:
        with worker_pool(workers) as pool:
            hashed = _collected(pool.map(hash_chunk, *arguments))
    in_order = [None] * len(hashed)
    for place, clk in zip(order, hashed, strict=True):
        in_order[place] = clk
    return in_order


def _collected(results: Iterable) -> list:
    """Collect every hash from ``results``, each chunk as hash_chunk returns it.

    The progress bar counts records, never chunks. With a record salt, the
    number of chunks depends on the salt values, so showing it could reveal
    something about them.
    """
    hashed = []
    for clks, _ in results:
        hashed.extend(clks)
        advance(len(clks))
    return hashed


def _unlocked(
    ctx: ValidationContext,
    secret_file: str,
    private_key: str,
    folder: Path | str,
    provenance: dict,
) -> str | None:
    """Unlock the secret in the site's secret file with the site's private key.

    Return None if that fails. ``ctx`` records the problem.
    """
    site = provenance["site"]
    locked = check_pprl_input(
        ctx,
        Label.SECRET,
        secret_file,
        folder,
        "secret",
        provenance=provenance,
        site=site,
    )
    private = check_pprl_input(
        ctx,
        Label.PRIVATE_KEY,
        private_key,
        folder,
        "private_key",
        provenance=provenance,
        site=site,
    )
    if not (locked and private):
        return None
    with ctx.attempt(Label.SECRET, PprlFileError):
        return _locking.unlock(locked, private)
    return None


def hash_with_key(
    secret_file: str,
    private_key: str,
    *,
    provenance: dict,
    data_folder: Path | str,
    output_folder: Path | str,
    patients: str | None = None,
    fields: dict | None = None,
    output: str | None = None,
    hash_length: int | None = None,
    fill_ratio: float | None = None,
    xor_folds: int = 0,
    project_salt: str | None = None,
    record_salt: dict | None = None,
) -> int:
    """Run ``pprl hash``: unlock the secret, then call ``hash_records`` with it.

    The secret is in ``secret_file`` and is unlocked with ``private_key``.
    Both files are in data_folder. The other arguments are passed on to
    ``hash_records``.
    """
    ctx = ValidationContext()
    secret = _unlocked(ctx, secret_file, private_key, data_folder, provenance)
    return hash_records(
        patients,
        secret,
        fields,
        output,
        provenance=provenance,
        data_folder=data_folder,
        output_folder=output_folder,
        hash_length=hash_length,
        fill_ratio=fill_ratio,
        xor_folds=xor_folds,
        project_salt=project_salt,
        record_salt=record_salt,
        ctx=ctx,
    )


def hash_records(
    patients: str | None = None,
    secret: str | None = None,
    fields: dict | None = None,
    output: str | None = None,
    *,
    provenance: dict,
    data_folder: Path | str,
    output_folder: Path | str,
    hash_length: int | None = None,
    fill_ratio: float | None = None,
    xor_folds: int = 0,
    project_salt: str | None = None,
    record_salt: dict | None = None,
    ctx: ValidationContext | None = None,
) -> int:
    """Hash patient records into Bloom filter hashes.

    Read the cleaned patient file that pprl clean wrote, and check it.
    Prepare each field according to its type. Then write a hash file with
    one hash per record. Each hash comes with the record's study_id and
    nothing else.

    Parameters
    ----------
    patients : str
        Filename of the cleaned patient file (in data_folder).
    secret : str
        Shared secret for HMAC-based hashing.
    fields : dict
        The columns to hash, each with its weight and type.
    output : str
        Output filename for the hash file (in output_folder).
    provenance : dict
        The ``project``, bundle ``set`` and ``site`` the file is for. The
        cleaned file must belong to them, and the hash file is written for them.
    hash_length : int
        The number of bits in each hash (see ``field_bits``).
    fill_ratio : float
        The bits that all fields together set, divided by ``hash_length``.
    xor_folds : int
        How many times to fold the hashes (see ``expand_schema``).
    project_salt : str
        The salt for every record's keys, if any (see ``expand_schema``).
    record_salt : dict
        The setting that salts each record's keys separately, if any (see
        ``record_salt_values``).
    ctx : ValidationContext
        Holds the problems already found, if any.

    Returns
    -------
    int
        0 on success, 1 on validation failure.
    """
    ctx = ValidationContext() if ctx is None else ctx
    site = check_site(ctx, provenance["site"])
    output_file_path = check_output(
        ctx, Label.OUTPUT_FILE, "output", output, output_folder
    )
    cleaned = check_pprl_input(
        ctx,
        Label.PATIENTS_FILE,
        patients,
        data_folder,
        "clean",
        provenance=provenance,
        site=site,
    )
    rows = check_patients(
        ctx, cleaned.members[Member.PATIENTS] if cleaned else None, patients, fields
    )
    check_hash_length(ctx, hash_length, fields)
    check_fill_ratio(ctx, fill_ratio)
    check_xor_folds(ctx, xor_folds)
    check_project_salt(ctx, project_salt)
    check_record_salt(ctx, record_salt, fields)
    if ctx.conclude():
        return 1
    # These are None only after a failure, which conclude has reported.
    if not isinstance(fields, dict) or rows is None or secret is None:
        raise AssertionError
    if hash_length is None or fill_ratio is None:
        raise AssertionError
    if output_file_path is None:
        raise AssertionError
    schema = from_json_dict(
        expand_schema(
            fields,
            hash_length=hash_length,
            fill_ratio=fill_ratio,
            xor_folds=xor_folds,
            project_salt=project_salt,
        )
    )

    stoppable("hash", len(rows))
    step(msg.STEP_HASHING, len(rows))
    hashed_data = _hash_rows(rows, secret, schema, record_salt=record_salt)
    output_df = rows[list(RECORD_COLUMNS)].copy()
    output_df["hash"] = [serialize_bitarray(x) for x in hashed_data]
    output_df = output_df[list(HASH_COLUMNS)]
    step(msg.STEP_WRITING_HASHES)
    _pprl_file.write(
        output_file_path,
        "hashes",
        {Member.HASHES: output_df},
        provenance=provenance,
        inputs=[cleaned] if cleaned else [],
    )

    finish_progress()
    ok_user(
        msg.WROTE_HASHES(
            count=len(output_df),
            site=site,
            output=display_name(output_file_path),
        )
    )
    return 0
