"""Build the clkhash schema and prepare field values for Bloom filter hashing."""

import base64
import re
from typing import TYPE_CHECKING

from anyascii import anyascii

from . import _messages as msg
from . import _wording
from ._dates import is_date
from ._shared import RECORD_COLUMNS, is_number

if TYPE_CHECKING:
    import pandas as pd

# Compiled once, because preprocessing calls it for every value.
_RE_NON_ALNUM = re.compile(r"[^A-Z0-9]")


# The field types, which decide how a field's values are prepared for hashing:
# - text ignores case, punctuation and word order.
# - date must be written yyyy-mm-dd and keeps its order, so that 1990-03-04
#   and 1990-04-03 hash differently.
# - zip must be 5 digits, and state must be 2 letters. This way a ZIP code
#   that lost its leading zero (2101 for 02101) is an error, rather than a
#   different value.
FIELD_TYPES = ("text", "date", "zip", "state")


def prepare_text(value: object) -> str:
    """Prepare a text value so that word order, punctuation and case don't matter.

    The steps are:
        1. anyascii  -- transliterate Unicode to ASCII
        2. upper()   -- convert to uppercase
        3. Replace each character that is not a letter or digit with a space
        4. Sort the words, and join them with single spaces

    After these steps, values that differ only in word order, punctuation,
    or case become the same, and so do their hashes. clkhash itself adds a
    space at each end of the value.

    Examples
    --------
        >>> prepare_text("O'Example Last-Name")
        'EXAMPLE LAST NAME O'
        >>> prepare_text("Last-Name O'Example")
        'EXAMPLE LAST NAME O'
        >>> prepare_text("1990-03-15")
        '03 15 1990'
        >>> prepare_text("02101-1234")
        '02101 1234'
        >>> prepare_text("")
        ''
        >>> prepare_text("--")
        ''
        >>> prepare_text(None)
        ''
        >>> prepare_text(12345)
        '12345'
    """
    if value is None:
        return ""
    return " ".join(
        sorted(_RE_NON_ALNUM.sub(" ", anyascii(str(value)).upper()).split())
    )


def prepare_date(value: str) -> str:
    """Prepare a date value by keeping its digits, in order.

    The value must be a date written yyyy-mm-dd.

    Examples
    --------
        >>> prepare_date("1990-03-04")
        '19900304'
        >>> prepare_date("")
        ''
    """
    return value.replace("-", "")


PREPARERS = {
    "text": prepare_text,
    "date": prepare_date,
    "zip": prepare_text,
    "state": prepare_text,
}

# For each type with a fixed format, a check of whether a non-empty value has
# that format.
FORMATS = {
    "date": is_date,
    "zip": re.compile(r"[0-9]{5}").fullmatch,
    "state": re.compile(r"[A-Z]{2}").fullmatch,
}


# ---------------------------------------------------------------------------
# Fields and hashing settings
#
# A project gives each field to be hashed a weight and a type. Each field
# then gets a share of the hash's bits in proportion to its weight (see
# ``field_bits``). This lets the coordinator choose the weights with pprl
# sweep and write them in project.yml in the same terms. pprl bundles copies
# them into each site's bundle.
#
# A project also sets how long its hashes are and how full, in project.yml:
# `hash_length` is the number of bits in each hash, and `fill_ratio` is the
# bits that all fields together set, divided by `hash_length` (see
# ``field_bits``). pprl has no defaults for either: like the weights, a
# project chooses its own, with pprl sweep. pprl bundles copies them into
# each site's bundle.
#
# A project may also fold its hashes, if project.yml sets `xor_folds` (see
# ``expand_schema``). Folded hashes score lower, so they need their own
# threshold. A project may also salt its keys, in one of two ways:
# - `project_salt` uses the same salt for every record (see ``expand_schema``).
# - `record_salt` salts each record by its own value of a field (see
#   ``record_salt_values``).
# Neither is used unless project.yml sets it.
#
# Everything else is fixed here and is not a setting: the key derivation
# (HKDF-SHA256, with a fixed label naming the protocol) and the encoding
# (clkhash's BLAKE2 encoding, blakeHash). No log holds any of the settings.
# ---------------------------------------------------------------------------

# The limits of `hash_length` and `fill_ratio`. A hash is stored as whole
# bytes, so its length is a multiple of 8.
HASH_LENGTH_MIN = 64
HASH_LENGTH_MAX = 65_536
FILL_RATIO_MAX = 4.0

_FIELD_KEYS = frozenset({"weight", "type"})
KDF = {
    "type": "HKDF",
    "hash": "SHA256",
    # HKDF's info: a fixed label that ties the keys to this use. It is not a
    # secret, and every project uses the same one; each project's own secret
    # (and its project_salt, if any) makes its keys its own.
    "info": base64.b64encode(b"pprl CLK v1").decode("ascii"),
    "keySize": 64,
}


def field_types(fields: object) -> dict:
    """Return each field's type. A field's type is "text" unless it says otherwise.

    Each field is written as ``name: weight``, or as
    ``name: {weight: ..., type: ...}`` for a field that is not text. Every
    weight must be a number above 0.

    Raise ValueError, with a message for the user, if ``fields`` does not
    have this form.

    >>> field_types({"name": 0.5, "dob": {"weight": 0.25, "type": "date"}})
    {'name': 'text', 'dob': 'date'}
    """
    if not isinstance(fields, dict) or not fields:
        raise ValueError(
            msg.FIELDS_NOT_SETTINGS(value=_wording.supplied(_wording.shown(fields)))
        )
    for name, config in fields.items():
        if isinstance(config, dict):
            unknown = sorted(map(str, config.keys() - _FIELD_KEYS))
            if unknown:
                raise ValueError(
                    msg.FIELD_UNKNOWN_KEYS(
                        field=_wording.supplied(name),
                        keys=[_wording.supplied(key) for key in unknown],
                    )
                )
            if "weight" not in config:
                raise ValueError(msg.FIELD_NO_WEIGHT(field=_wording.supplied(name)))
            if config.get("type", "text") not in FIELD_TYPES:
                raise ValueError(
                    msg.FIELD_BAD_TYPE(
                        field=_wording.supplied(name),
                        value=_wording.supplied(_wording.shown(config["type"])),
                        types=_wording.listed(FIELD_TYPES),
                    )
                )
        weight = _weight(config)
        if not is_number(weight) or weight <= 0:
            raise ValueError(
                msg.FIELD_BAD_WEIGHT(
                    field=_wording.supplied(name),
                    value=_wording.supplied(_wording.shown(weight)),
                )
            )
    return {
        name: config.get("type", "text") if isinstance(config, dict) else "text"
        for name, config in fields.items()
    }


def _weight(config: dict) -> object:
    return config.get("weight") if isinstance(config, dict) else config


def field_weights(fields: dict) -> dict:
    """Return each field's weight. ``fields`` has the form ``field_types`` accepts.

    >>> field_weights({"name": 0.5, "dob": {"weight": 0.25, "type": "date"}})
    {'name': 0.5, 'dob': 0.25}
    """
    return {name: _weight(config) for name, config in fields.items()}


def field_bits(fields: dict, hash_length: int, fill_ratio: float) -> dict:
    """Return the number of bits for each field.

    Each field gets a share of ``fill_ratio`` × ``hash_length`` in proportion
    to its weight, and at least 1 bit. ``fields`` has the form
    ``field_types`` accepts.

    >>> fields = {"name": 0.5, "dob": {"weight": 0.25, "type": "date"}}
    >>> field_bits(fields, 1024, 1.0)
    {'name': 683, 'dob': 341}
    """
    weights = field_weights(fields)
    total = sum(weights.values())
    return {
        name: max(1, round(weight / total * fill_ratio * hash_length))
        for name, weight in weights.items()
    }


def record_salt_values(rows: pd.DataFrame, record_salt: dict) -> pd.Series:
    """Return each row's salt value for a project's `record_salt` setting.

    The value is the row's ``field``, as ``check_patients`` prepared it,
    shortened to ``length`` characters if a length is given.

    Only records with the same salt value can match. If two sites' records
    of the same patient have different values, the match is lost. A record
    with no value gets the empty salt, the same as every other such record.

    >>> import pandas as pd
    >>> rows = pd.DataFrame({"zip": ["02101", "", "10001"]})
    >>> list(record_salt_values(rows, {"field": "zip", "length": 3}))
    ['021', '', '100']
    >>> list(record_salt_values(rows, {"field": "zip"}))
    ['02101', '', '10001']
    """
    return rows[record_salt["field"]].str[: record_salt.get("length")]


def expand_schema(
    fields: dict,
    *,
    hash_length: int,
    fill_ratio: float,
    xor_folds: int = 0,
    project_salt: str | None = None,
) -> dict:
    """Make the clkhash schema for ``fields`` and the project's hashing settings.

    ``fields`` and the settings must already be checked. Each field's bits
    come from ``hash_length`` and ``fill_ratio`` (see ``field_bits``).

    clkhash hashes into ``hash_length`` × 2 ** xor_folds bits, then folds
    them down to ``hash_length``. This way each field gets the same number of bits
    however many folds there are. clkhash reads the key ``xor_folds``. The
    ``xorFolds`` key from clkhash's own JSON schema passes validation but is
    ignored.

    ``project_salt``, if given, becomes HKDF's salt, stored in the schema as
    base64.

    >>> sizes = {"hash_length": 512, "fill_ratio": 1.5}
    >>> schema = expand_schema({"name": 1}, **sizes)
    >>> schema["clkConfig"]["l"], len(schema["features"])  # 2 ignored + name
    (512, 3)
    >>> schema["features"][2]["hashing"]["strategy"]["bitsPerFeature"]
    768
    >>> folded = expand_schema({"name": 1}, **sizes, xor_folds=1)
    >>> folded["clkConfig"]["xor_folds"]
    1
    >>> "salt" in schema["clkConfig"]["kdf"]
    False
    >>> salted = expand_schema({"name": 1}, **sizes, project_salt="a")
    >>> salted["clkConfig"]["kdf"]["salt"]
    'YQ=='
    """
    kdf = KDF
    if project_salt is not None:
        salt = base64.b64encode(project_salt.encode("utf-8")).decode("ascii")
        kdf = KDF | {"salt": salt}
    features = [
        {"identifier": "", "ignored": True},
        *({"identifier": column, "ignored": True} for column in RECORD_COLUMNS),
    ]
    for name, bits in field_bits(fields, hash_length, fill_ratio).items():
        features.append(
            {
                "identifier": name,
                "format": {"type": "string", "encoding": "utf-8", "case": "upper"},
                "hashing": {
                    "comparison": {"type": "ngram", "n": 2},
                    "strategy": {"bitsPerFeature": bits},
                    "hash": {"type": "blakeHash"},
                },
                "missingValue": {"sentinel": ""},
            }
        )
    return {
        "version": 3,
        "clkConfig": {"l": hash_length, "xor_folds": xor_folds, "kdf": kdf},
        "features": features,
    }
