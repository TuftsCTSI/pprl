"""Measure how strong a linkage is.

Link reports these measures and writes them to the linkage report's
``quality`` member, which the linkage agent sends to the coordinator. They
are aggregates: counts and shares, never a record.

Only ``threshold`` decides which records link. The review band extends
REVIEW_BAND on either side of the threshold (see ``band``). For each pair
of sites, link reports what falls around the threshold:

* Weak pairs: linked pairs that score between the threshold and the top of
  the band. Raising the threshold to the top would split them. They are
  counted exactly.
* Near misses: records not linked to the other site whose best score there
  is between the bottom of the band and the threshold. Lowering the
  threshold to the bottom would link them. They are estimated from a sample
  of each site's records, because finding every
  record's best score would double link's comparisons.
* A rough estimate of false pairs, based on the records with several
  candidates (see ``false_pairs``). Missed pairs are left for the
  coordinator to judge from the overlap the study expects.
* Warnings: a pair of sites that links nearly every record of the smaller
  site, has many false pairs, or has mostly weak pairs.

Before comparing anything, link stops if two sites hashed with different
secrets (see ``bit_correlations``).

The sample's best scores are kept as a single histogram with bins of
width BIN, combining all sites. ``pprl review`` reads the linkage report and
draws it alongside the other linkage details.
"""

import itertools
import math
from typing import TYPE_CHECKING

import numpy as np

from . import _messages as msg
from . import _wording

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from bitarray import bitarray

BIN = 0.01  # The width of each bin in a histogram of scores
REVIEW_BAND = 0.05  # How far on either side of the threshold link reports on
SAMPLE = 20_000  # How many records per site get their best score found
# Link warns when a share goes above these limits (see warned).
MOST_LINKED = 0.95
MANY_FALSE = 0.10
MOSTLY_WEAK = 0.5
# Below this correlation, two sites did not hash with the same secret.
# (This may not apply to all hash configurations)
SAME_SECRET = 0.3
# With fewer records than this at a site, its bits say too little to check.
SAME_SECRET_RECORDS = 1000
# How many hashes bit_correlations counts at a time.
_BIT_CHUNK = 10_000
# How many steps false_pairs uses when searching for its answer.
_FALSE_STEPS = 4_000


def band(threshold: float) -> tuple:
    """Return the range of scores around ``threshold`` that link reports on.

    The range extends REVIEW_BAND on either side, limited to 0 to 1.

    >>> band(0.85), band(0.98)
    ((0.8, 0.9), (0.93, 1.0))
    """
    return (
        round(max(threshold - REVIEW_BAND, 0.0), 6),
        round(min(threshold + REVIEW_BAND, 1.0), 6),
    )


def histogram(scores: np.ndarray) -> list:
    """Count how many ``scores`` fall in each bin of width BIN, from 0 to 1.

    A score of 1 goes in the last bin.

    >>> found = histogram([0.0, 0.805, 0.8099, 1.0])
    >>> found[0], found[80], found[99], sum(found)
    (1, 2, 1, 4)
    """
    bins = round(1 / BIN)
    places = np.floor(np.asarray(scores, dtype=float) / BIN + 1e-9)
    places = np.clip(places.astype(int), 0, bins - 1)
    return np.bincount(places, minlength=bins).tolist()


def _bit_shares(hashes: list[bitarray]) -> np.ndarray:
    """Return the share of a site's ``hashes`` that set each bit."""
    counts = 0
    for chunk in itertools.batched(hashes, _BIT_CHUNK, strict=False):
        rows = np.frombuffer(b"".join(chunk), np.uint8).reshape(len(chunk), -1)
        counts = counts + np.unpackbits(rows, axis=1).sum(axis=0, dtype=np.int64)
    return np.asarray(counts / len(hashes))


def bit_correlations(hashes: list[list[bitarray]]) -> dict:
    """Correlate the bits set at each pair of sites, keyed by position in ``hashes``.

    For each pair, this is the correlation between how often each bit is set
    at one site and how often it is set at the other, rounded. It is None if
    a site has too few records, or if a site's bit shares never vary.

    A common bigram sets the same bits at every site that hashes with the
    same secret. So two such sites agree, whoever their patients are. With
    different secrets, the sites' bits are unrelated. The linkage agent
    could compute this from the hashes it already holds, so the check
    reveals nothing new.
    """
    shares = [_bit_shares(h) if len(h) >= SAME_SECRET_RECORDS else None for h in hashes]
    found = {}
    for i, j in itertools.combinations(range(len(hashes)), 2):
        first, second = shares[i], shares[j]
        apart = first is None or second is None or not (first.std() and second.std())
        found[(i, j)] = (
            None if apart else round(float(np.corrcoef(first, second)[0, 1]), 3)
        )
    return found


def same_secret(correlation: float | None) -> bool:
    """Tell whether a pair of sites' correlation shows they share one secret.

    See ``bit_correlations``. A correlation of None shows nothing, so it
    counts as a pass.
    """
    return correlation is None or correlation >= SAME_SECRET


def datasets_in(group: Iterable[tuple[int, int]]) -> set:
    """List the datasets that a linked group of (dataset, record) pairs draws on.

    >>> datasets_in([(0, 12), (2, 5)])
    {0, 2}
    """
    return {dataset for dataset, _ in group}


def linked_between(linked: list[set[int]], first: int, second: int) -> int:
    """Count the groups that link the datasets ``first`` and ``second``.

    ``linked`` gives each group's datasets (see ``datasets_in``).
    """
    return sum(1 for datasets in linked if {first, second} <= datasets)


def other_site(pair: dict, site: str) -> str:
    """Return the site in ``pair`` that is not ``site``.

    ``pair`` has the form that ``quality`` holds.
    """
    first, second = pair["sites"]
    return second if site == first else first


def group_strengths(pairs: tuple, groups: list, sizes: list[int]) -> tuple:
    """Find each group's strength, which is the score of its weakest pair.

    A group's strength is the lowest score among the candidate ``pairs``
    between its records. ``pairs`` is anonlink's (scores, datasets, records).
    Return the strengths by group, and the pairs within groups as (scores,
    first dataset, second dataset).
    """
    scores, (d0, d1), (r0, r1) = (np.asarray(pairs[0]), *pairs[1:])
    d0, d1, r0, r1 = (np.asarray(a, dtype=np.int64) for a in (d0, d1, r0, r1))
    width = max(sizes, default=0) + 1
    group_of = np.full(len(sizes) * width, -1, dtype=np.int64)
    for number, group in enumerate(groups):
        for dataset, record in group:
            group_of[dataset * width + record] = number
    first, second = group_of[d0 * width + r0], group_of[d1 * width + r1]
    within = (first >= 0) & (first == second)
    strengths = np.full(len(groups), np.inf)
    np.minimum.at(strengths, first[within], scores[within])
    return strengths, (scores[within], d0[within], d1[within])


def false_pairs(
    candidates: Sequence[float], without: Sequence[float], several: float
) -> int | None:
    """Estimate how many false pairs a pair of sites has.

    Return None if so many records have several candidates that no number of
    false pairs can explain it.

    For each of the two sites, ``candidates`` records have a candidate at the
    other site, and ``without`` records have none. ``several`` records, across
    both sites, have two or more candidates.

    Suppose F false pairs fall on records that have no true counterpart.
    Then a share p = F / (without + F) of records finds a false candidate.
    Records that do have a counterpart and also find a false candidate make
    up ``several``, so ``several`` is the sum of (candidates - F) * p. This
    assumes that records with a counterpart find false candidates as often as
    records without one.

    >>> false_pairs([4_500, 4_500], [95_500, 95_500], 34)
    398
    >>> false_pairs([4_500, 4_500], [95_500, 95_500], 2_000) is None
    True
    """
    if not several:
        return 0
    grid = np.linspace(0, min(candidates), _FALSE_STEPS + 1)
    implied = sum(
        (found - grid) * grid / np.maximum(none + grid, 1)
        for found, none in zip(candidates, without, strict=True)
    )
    enough = np.nonzero(implied >= several)[0]
    return round(float(grid[enough[0]])) if len(enough) else None


def _count_interval(count: float, z: float = 1.96) -> tuple:
    """Return a 95% interval for a ``count`` of rare events.

    This uses Wilson and Hilferty's approximation to the Poisson distribution.

    >>> [round(value, 1) for value in _count_interval(25)]
    [16.2, 36.9]
    """

    def edge(value: float, sign: int) -> float:
        return value * (1 - 1 / (9 * value) + sign * z / (3 * math.sqrt(value))) ** 3

    return (edge(count, -1) if count else 0.0, edge(count + 1, 1))


def false_estimate(
    candidates: Sequence[float], without: Sequence[float], several: float
) -> dict:
    """Return ``false_pairs`` with an interval based on the count ``several``.

    A value that cannot be found is None. The interval reflects only the
    uncertainty in that count, not in the estimate's assumption.
    """
    low, high = _count_interval(several)
    return {
        "estimate": false_pairs(candidates, without, several),
        "low": false_pairs(candidates, without, low),
        "high": false_pairs(candidates, without, high),
    }


def summary(
    sites: list[str],
    sizes: list[int],
    threshold: float,
    pairs: tuple,
    groups: list,
    *,
    linked: list[set[int]],
    best: dict,
    correlations: dict | None,
    secret_check: bool = True,
) -> dict:
    """Summarize what link found, for the linkage report's ``quality`` member.

    - ``linked``: for each group, the datasets it has records from (see
      ``datasets_in``).
    - ``pairs``: anonlink's candidate pairs.
    - ``best``: for each (dataset, other dataset), the sampled records'
      indices, their best scores at the other dataset, and whether each is
      linked to a record there.
    - ``correlations``: for each pair of datasets, the result of
      ``bit_correlations``.
    - ``secret_check``: whether link ran the same-secret check. Without it,
      there are no correlations.
    """
    low, high = band(threshold)
    _, (within, w0, w1) = group_strengths(pairs, groups, sizes)
    _, (d0, d1), (r0, r1) = pairs
    d0, d1, r0, r1 = (np.asarray(a, dtype=np.int64) for a in (d0, d1, r0, r1))
    found = []
    for i, j in itertools.combinations(range(len(sites)), 2):
        between = ((w0 == i) & (w1 == j)) | ((w0 == j) & (w1 == i))
        scores = within[between]
        pair = {
            "sites": [sites[i], sites[j]],
            "linked": linked_between(linked, i, j),
            "linked_pairs": int(between.sum()),
            "weak": int((scores < high).sum()),
            "bit_correlation": correlations[i, j] if correlations else None,
            "records": {sites[i]: sizes[i], sites[j]: sizes[j]},
            "candidates": {},
            "ambiguous": {},
            "near": {},
        }
        for a, b in ((i, j), (j, i)):
            at = ((d0 == a) & (d1 == b), r0), ((d0 == b) & (d1 == a), r1)
            records = np.concatenate([index[mask] for mask, index in at])
            counts = np.bincount(records, minlength=sizes[a]) if len(records) else []
            pair["candidates"][sites[a]] = int((np.asarray(counts) >= 1).sum())
            pair["ambiguous"][sites[a]] = int((np.asarray(counts) > 1).sum())
            sampled, scores_at, is_linked = best[(a, b)]
            near = (~is_linked) & (scores_at >= low) & (scores_at < threshold)
            share = sizes[a] / len(sampled) if len(sampled) else 0
            pair["near"][sites[a]] = {
                "sample": len(sampled),
                "in_sample": int(near.sum()),
                "estimate": round(int(near.sum()) * share),
            }
        with_one = [pair["candidates"][site] for site in pair["sites"]]
        pair["false"] = false_estimate(
            with_one,
            [
                pair["records"][site] - found_at
                for site, found_at in zip(pair["sites"], with_one, strict=True)
            ],
            sum(pair["ambiguous"].values()),
        )
        found.append(pair)
    return {
        "threshold": threshold,
        "review_band": [low, high],
        "secret_check": secret_check,
        "pairs": found,
        "best": np.sum(
            [histogram(scores_at) for _, scores_at, _ in best.values()], axis=0
        ).tolist(),
    }


def warned(quality: dict) -> list:
    """List the warnings that a linkage's ``quality`` calls for, as messages."""
    shown = []
    for pair in quality["pairs"]:
        first, second = pair["sites"]
        sizes_by_site = pair["records"]
        smaller = min(pair["sites"], key=lambda s: sizes_by_site[s])
        if pair["linked"] and pair["linked"] > MOST_LINKED * sizes_by_site[smaller]:
            share = pair["linked"] / sizes_by_site[smaller]
            shown.append(
                msg.LINK_MOST(first=first, second=second, share=share, smaller=smaller)
            )
        false = pair["false"]["estimate"]
        if false is None:
            shown.append(msg.LINK_MOSTLY_FALSE(first=first, second=second))
        elif pair["linked_pairs"] and false > MANY_FALSE * pair["linked_pairs"]:
            shown.append(
                msg.LINK_MANY_FALSE(
                    first=first, second=second, share=false / pair["linked_pairs"]
                )
            )
        if pair["linked_pairs"] and pair["weak"] > MOSTLY_WEAK * pair["linked_pairs"]:
            shown.append(
                msg.LINK_MOSTLY_WEAK(
                    first=first,
                    second=second,
                    share=pair["weak"] / pair["linked_pairs"],
                )
            )
    return shown


def shown_limit(high: float | None) -> str:
    """Format the top of a false-pair interval for a message.

    >>> shown_limit(1200), shown_limit(None) == msg.FALSE_NO_LIMIT
    ('1,200', True)
    """
    return msg.FALSE_NO_LIMIT if high is None else f"{high:,}"


def reported(quality: dict) -> list:
    """List what to show about a linkage's ``quality``, for each pair of sites."""
    low, high = quality["review_band"]
    threshold = quality["threshold"]
    lines = []
    for pair in quality["pairs"]:
        first, second = pair["sites"]
        lines.append(
            msg.LINK_WEAK(
                first=first,
                second=second,
                weak=pair["weak"],
                threshold=threshold,
                high=high,
            )
        )
        for site, near in pair["near"].items():
            lines.append(
                msg.LINK_NEAR(
                    site=site,
                    other=other_site(pair, site),
                    near=near["estimate"],
                    low=low,
                    threshold=threshold,
                    sample=near["sample"],
                )
            )
        false = pair["false"]
        if false["estimate"] is not None:
            lines.append(
                msg.LINK_FALSE(
                    first=first,
                    second=second,
                    false=false["estimate"],
                    low=false["low"],
                    high=shown_limit(false["high"]),
                )
            )
    return lines


def info_lines(quality: dict) -> list:
    """Write the [INFO] lines for a linkage's ``quality``.

    They go to the linkage agent's own log, for troubleshooting. ``pprl
    review`` reads the linkage report instead.
    """
    low, high = quality["review_band"]
    lines = [msg.INFO_LINK_BAND(threshold=quality["threshold"], low=low, high=high)]
    for pair in quality["pairs"]:
        first, second = pair["sites"]
        correlation = pair["bit_correlation"]
        if not quality["secret_check"]:
            correlation = msg.INFO_OFF
        elif correlation is None:
            correlation = msg.INFO_UNKNOWN
        lines += [
            msg.INFO_LINK_SECRET(first=first, second=second, correlation=correlation),
            msg.INFO_LINK_PAIR(
                first=first,
                second=second,
                linked=pair["linked"],
                first_records=pair["records"][first],
                second_records=pair["records"][second],
            ),
            msg.INFO_LINK_FALSE(
                first=first,
                second=second,
                **{
                    key: msg.INFO_UNKNOWN if value is None else value
                    for key, value in pair["false"].items()
                },
                several=sum(pair["ambiguous"].values()),
                candidates=sum(pair["candidates"].values()),
            ),
        ]
    lines.append(msg.INFO_LINK_BEST(counts=_wording.listed(quality["best"])))
    for warning in warned(quality):
        lines.append(msg.INFO_LINK_WARNING(text=warning.partition("\n")[0]))
    return lines
