"""Compute linkage quality metrics: density, precision, recall and F1.

These are plain numeric computations on hash and linkage DataFrames. The
module imports nothing from pprl, so any module can use it.
"""

from typing import TYPE_CHECKING

import numpy as np
from clkhash.serialization import deserialize_bitarray

if TYPE_CHECKING:
    import pandas as pd


def hash_bits(encoded: str) -> int:
    """Count the bits in a hash (a serialized CLK).

    >>> from bitarray import bitarray
    >>> from clkhash.serialization import serialize_bitarray
    >>> hash_bits(serialize_bitarray(bitarray("10101010")))
    8
    """
    return len(deserialize_bitarray(encoded))


def density_stats(hash_df: pd.DataFrame) -> dict[str, float]:
    """Compute Bloom filter density statistics from a hash DataFrame.

    Density is the proportion of bits set to 1 in each hash.
    Filters that are too sparse expose individual bigram contributions
    to frequency attacks; filters that are too saturated carry little
    discriminative information.

    Parameters
    ----------
    hash_df : DataFrame
        Hash output with a 'hash' column containing serialized bitarrays.

    Returns
    -------
    dict with keys: density_mean, density_min, density_max

    Examples
    --------
        >>> import pandas as pd
        >>> from clkhash.serialization import serialize_bitarray
        >>> from bitarray import bitarray
        >>> ba = bitarray('10101010')
        >>> df = pd.DataFrame({'hash': [serialize_bitarray(ba)]})
        >>> result = density_stats(df)
        >>> result['density_mean']
        0.5
        >>> result['density_min']
        0.5
        >>> result['density_max']
        0.5
    """
    hashes = hash_df["hash"]
    if len(hashes) == 0:
        return {"density_mean": 0.0, "density_min": 0.0, "density_max": 0.0}

    n_bits = hash_bits(hashes.iloc[0])
    densities = (
        np.array([deserialize_bitarray(s).count(1) for s in hashes], dtype=np.float64)
        / n_bits
    )

    return {
        "density_mean": float(densities.mean()),
        "density_min": float(densities.min()),
        "density_max": float(densities.max()),
    }


def count_true_pairs(record_dfs: list[pd.DataFrame], column: str = "study_id") -> int:
    """Count the true matching pairs across sites, which is the ground truth.

    Two records at different sites belong to the same person exactly when
    they share an id in ``column``. This holds only for test data whose ids
    mean the same person at every site. Such ids are the person_ids that
    synth writes with ``person_ids: shared``, or study_ids made from them.
    Two records from the same site are never counted as a pair.

    Parameters
    ----------
    record_dfs : list of DataFrame
        One patient or hash DataFrame per site, each with ``column``.
    column : str
        The id column.

    Examples
    --------
        >>> import pandas as pd
        >>> a = pd.DataFrame({"study_id": ["1", "2", "3"]})
        >>> b = pd.DataFrame({"study_id": ["2", "3"]})
        >>> c = pd.DataFrame({"study_id": ["3", "9"]})
        >>> count_true_pairs([a, b, c])
        4
    """
    sites_by_id = {}
    for site, df in enumerate(record_dfs):
        for record_id in df[column].astype(str):
            sites_by_id.setdefault(record_id, set()).add(site)
    return sum(len(s) * (len(s) - 1) // 2 for s in sites_by_id.values())


def precision_recall(
    linkage_df: pd.DataFrame, n: int, column: str = "study_id"
) -> dict:
    """Compute precision and recall from a linkage DataFrame.

    Ground truth: records at different sites with the same id in ``column``
    are the same person (see ``count_true_pairs``). Every cross-site pair
    in a linkage group is a true positive if the two records share an id,
    and a false positive otherwise.

    Parameters
    ----------
    linkage_df : DataFrame
        Linkage output in long format with columns: group_id, site, and
        ``column``.
    n : int
        Total number of true matching pairs (from ``count_true_pairs``).
    column : str
        The id column.

    Returns
    -------
    dict with keys: precision, recall, tp, fp, fn

    Examples
    --------
        >>> import pandas as pd
        >>> linkages = pd.DataFrame({
        ...     "group_id": ["1", "1", "2", "2"],
        ...     "site": ["a", "b", "a", "b"],
        ...     "study_id": ["2", "2", "1", "3"],
        ... })
        >>> result = precision_recall(linkages, n=2)
        >>> result["tp"], result["fp"], result["fn"]
        (1, 1, 1)
    """
    tp = 0
    fp = 0
    for _, group in linkage_df.groupby("group_id"):
        by_site = {}
        for site, record_id in zip(
            group["site"], group[column].astype(str), strict=True
        ):
            by_site.setdefault(site, set()).add(record_id)

        sites = list(by_site.keys())
        for i in range(len(sites)):
            for j in range(i + 1, len(sites)):
                ids_a = by_site[sites[i]]
                ids_b = by_site[sites[j]]
                matched = ids_a & ids_b
                tp += len(matched)
                fp += len(ids_a) * len(ids_b) - len(matched)

    fn = n - tp
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    return {"precision": precision, "recall": recall, "tp": tp, "fp": fp, "fn": fn}


def f1_score(precision: float, recall: float) -> float:
    """Return the harmonic mean of precision and recall.

    Examples
    --------
        >>> f1_score(1.0, 1.0)
        1.0
        >>> f1_score(0.0, 0.0)
        0.0
        >>> f1_score(0.5, 0.5)
        0.5
    """
    return (
        (2 * precision * recall / (precision + recall))
        if (precision + recall) > 0
        else 0.0
    )
