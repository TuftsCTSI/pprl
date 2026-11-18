"""Quickly generate patients and clinical tables for the benchmarks.

These records are for timing the commands, not for testing linkage.
"""

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_SYLLABLES = np.array(
    [
        "ka",
        "lo",
        "mi",
        "re",
        "sa",
        "to",
        "vi",
        "na",
        "de",
        "ru",
        "po",
        "li",
        "ga",
        "be",
        "ne",
        "ma",
        "jo",
        "ta",
        "si",
        "fe",
        "ko",
        "la",
        "mo",
        "ri",
        "cha",
        "bri",
        "sten",
        "wil",  # codespell:ignore
        "ar",
        "en",
        "ol",
        "ix",
        "um",
    ]
)
_CITIES = np.array(
    [
        "Boston",
        "Salem",
        "Lowell",
        "Worcester",
        "Quincy",
        "Medford",
        "Newton",
        "Waltham",
        "Lynn",
        "Malden",
        "Brockton",
        "Revere",
        "Chelsea",
        "Everett",
        "Somerville",
        "Cambridge",
        "Framingham",
    ]
)
_STATES = np.array(["MA", "NH", "RI", "CT", "VT", "ME"])


def _words(rng: np.random.Generator, n: int, syllables: int) -> pd.Series:
    """Make ``n`` capitalized made-up words of ``syllables`` syllables each."""
    parts = rng.choice(_SYLLABLES, size=(n, syllables))
    return pd.Series(["".join(p) for p in parts]).str.capitalize()


def people(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Make ``n`` made-up people with name, city, state, zip and dob."""
    days = rng.integers(0, 80 * 365, n)
    return pd.DataFrame(
        {
            "name": _words(rng, n, 2) + " " + _words(rng, n, 3),
            "city": rng.choice(_CITIES, n),
            "state": rng.choice(_STATES, n),
            "zip": [f"{z:05d}" for z in rng.integers(1000, 99999, n)],
            "dob": (np.datetime64("1940-01-01") + days.astype("timedelta64[D]")).astype(
                str
            ),
        }
    )


def _typo(names: pd.Series, rng: np.random.Generator, share: float) -> pd.Series:
    """Return ``names`` with one letter changed in a ``share`` of them."""
    names = names.copy()
    for i in np.flatnonzero(rng.random(len(names)) < share):
        name = names.iloc[i]
        at = rng.integers(1, len(name))
        names.iloc[i] = name[:at] + rng.choice(list("aeiou")) + name[at + 1 :]
    return names


def sites(
    n: int, shared: float = 0.5, typos: float = 0.2, seed: int = 2026
) -> dict[str, pd.DataFrame]:
    """Make two sites' patient files of ``n`` rows each.

    Each site uses its own person_ids. A ``shared`` share of the people are
    at both sites, and a ``typos`` share of those have a letter changed in
    their name at site_b. Return {site: DataFrame}, and the ids of each
    person who is at both sites.
    """
    rng = np.random.default_rng(seed)
    both = int(n * shared)
    everyone = people(2 * n - both, rng)
    a = everyone.iloc[:n].reset_index(drop=True)
    b = everyone.iloc[n - both :].reset_index(drop=True)
    b.loc[: both - 1, "name"] = _typo(b.loc[: both - 1, "name"], rng, typos).to_numpy()
    files = {}
    for site, df in (("site_a", a), ("site_b", b)):
        ids = rng.permutation(n) + 1
        files[site] = df.assign(person_id=[f"{site[-1]}{i}" for i in ids])
        files[site] = files[site][["person_id", *people(0, rng).columns]]
    return files


# A study's tables, for the study years only: demographics, with a row per
# patient; residence, with a row per year; and measures, with a
# row per measure and year, as dense as it can be.
YEARS = ("2023", "2024", "2025")
MEASURES = tuple(f"Measure {d:02d}" for d in range(1, 29))
ROWS_PER_PATIENT = {"demographics": 1, "residence": 3, "measures": 84}
_BATCH = 20_000  # Patients written at a time, so a table is never held whole


def write_tables(
    patients: pd.DataFrame,
    folder: Path,
    write: Callable[[pd.DataFrame, Path, bool], None],
    seed: int = 2026,
) -> None:
    """Write each table for ``patients`` into ``folder``, as <table>.csv.

    ``patients`` is a patient file from sites(). Write one batch of patients
    at a time, using ``write(df, path, header)``.
    """
    rng = np.random.default_rng(seed)
    for start in range(0, len(patients), _BATCH):
        ids = patients["person_id"].to_numpy()[start : start + _BATCH]
        for name, df in _batch(ids, rng).items():
            write(df, folder / f"{name}.csv", start == 0)


def _batch(ids: pd.Series, rng: np.random.Generator) -> dict:
    """Return every table's rows for the patients ``ids``."""
    n = len(ids)
    years = np.tile(YEARS, n)
    per_year = np.repeat(ids, len(YEARS))
    cells = len(MEASURES) * len(YEARS)
    status = rng.choice(["-1", "0", "1"], n * cells)
    return {
        "demographics": pd.DataFrame(
            {"person_id": ids, "birth_year": rng.integers(1930, 2020, n).astype(str)}
        ),
        "residence": pd.DataFrame(
            {
                "person_id": per_year,
                "year": years,
                "age_group": rng.choice(["0-17", "18-64", "65+"], len(per_year)),
                "zip5": [f"{z:05d}" for z in rng.integers(1000, 99999, len(per_year))],
            }
        ),
        "measures": pd.DataFrame(
            {
                "person_id": np.repeat(ids, cells),
                "measure": np.tile(np.repeat(MEASURES, len(YEARS)), n),
                "year": np.tile(YEARS, n * len(MEASURES)),
                "status": status,
                "value": np.where(status == "1", "yes", ""),
            }
        ),
    }


# Each site's bundle lists its tables' columns, and the merge agent's bundle
# says how to merge them, as in a study. Each table in a site's bundle
# uses a study's kinds of rule: the study's years, and every row of a
# patient and year that is in residence.
_IN_RESIDENCE = {"found_in": {"table": "residence", "on": ["person_id", "year"]}}
SITE_TABLES = {
    "demographics": {
        "columns": ["person_id", "birth_year"],
        "primary_key": ["person_id"],
        "keep": [{"found_in": {"table": "residence", "on": ["person_id"]}}],
        "censor": [],
    },
    "residence": {
        "columns": ["person_id", "year", "age_group", "zip5"],
        "primary_key": ["person_id", "year"],
        "keep": [{"not_empty": "zip5"}, {"one_of": {"year": list(YEARS)}}],
        "censor": [],
    },
    "measures": {
        "columns": ["person_id", "measure", "year", "status", "value"],
        "primary_key": ["person_id", "measure", "year"],
        "keep": [_IN_RESIDENCE, {"one_of": {"measure": list(MEASURES)}}],
        "censor": [],
    },
}
_FIRST_SITE = {"rank_by": [{"take_first_site": {}}]}
_BY_STATUS = {
    "column": {
        "status": {
            "rank_by": [{"take_top_ordinal": {"column": "status", "scale": [-1, 0, 1]}}]
        },
        "value": {"tether": "status"},
    }
}
MERGE_TABLES = {
    "demographics": {
        "primary_key": ["person_id"],
        "column": {"birth_year": _FIRST_SITE},
    },
    "residence": {
        "primary_key": ["person_id", "year"],
        "column": {"age_group": _FIRST_SITE, "zip5": _FIRST_SITE},
    },
    "measures": {
        "primary_key": ["person_id", "measure", "year"],
        "partition_by": "measure",
        "partitions": dict.fromkeys(MEASURES, _BY_STATUS),
    },
}
