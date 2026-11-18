"""Derive random streams from the scenario's seed.

Each individual has one random stream for their true record, and one for
their record at each site. They also have two for each clinical module they
have: one for the history the module draws, and one for which sites record
it. Draws from a stream happen in a fixed order, so the same scenario
always gives the same data. Adding or removing an individual never changes
anyone else's draws.

Streams are ``random.Random`` objects seeded with an integer, and every draw
goes through ``Random.random()``. Python guarantees that this sequence is
the same on every platform and in every Python version. So the output
depends only on the scenario (and, for names and addresses, on the pinned
Mimesis version).
"""

import hashlib
import random
from typing import TYPE_CHECKING

from pprl._shared import SEPARATOR

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


def derive_seed(scenario_seed: int | str, *labels: object) -> int:
    """Derive a 64-bit seed from the scenario seed and labels.

    Labels are values such as a person_id or a site.

    >>> derive_seed(20260926, 1) == derive_seed("20260926", "1")
    True
    >>> derive_seed(20260926, 1) == derive_seed(20260926, 2)
    False
    """
    text = SEPARATOR.join(str(part) for part in (scenario_seed, *labels))
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def seeded_rng(scenario_seed: int | str, *labels: object) -> random.Random:
    """Make a random number generator, seeded from the scenario seed and ``labels``."""
    return random.Random(derive_seed(scenario_seed, *labels))  # noqa: S311 (synthetic data, seeded to repeat)


def happens(rng: random.Random, rate: float) -> bool:
    """Return True with probability ``rate``.

    Always make a draw, whatever the rate, so the stream stays in step.
    """
    return rng.random() < rate


def draw_index(rng: random.Random, count: int) -> int:
    """Draw a uniform integer in ``range(count)``."""
    return min(int(rng.random() * count), count - 1)


def draw_between(rng: random.Random, low: int, high: int) -> int:
    """Draw a uniform integer from ``low`` to ``high``, both included."""
    return low + draw_index(rng, high - low + 1)


def draw_choice[T](rng: random.Random, values: Sequence[T]) -> T:
    """Draw one of ``values`` uniformly."""
    return values[draw_index(rng, len(values))]


def draw_tenths(rng: random.Random, low: float, high: float) -> float:
    """Draw a uniform number from ``low`` to ``high`` (inclusive) in steps of 0.1."""
    return draw_between(rng, round(low * 10), round(high * 10)) / 10


def shuffled(rng: random.Random, items: Iterable) -> list:
    """Shuffle the items with a Fisher-Yates shuffle that uses draw_index.

    >>> shuffled(random.Random(1), [1, 2, 3]) == shuffled(random.Random(1), [1, 2, 3])
    True
    """
    items = list(items)
    for i in range(len(items) - 1, 0, -1):
        j = draw_index(rng, i + 1)
        items[i], items[j] = items[j], items[i]
    return items


def first_of_rates(rng: random.Random, rates: list) -> int:
    """Choose the position of one rate, with that rate's chance.

    The rates must add up to 1.
    """
    ticket = rng.random()
    for position, rate in enumerate(rates):
        if ticket < rate:
            return position
        ticket -= rate
    return len(rates) - 1


# Site person ids are scrambled into 1 .. SCRAMBLED_ID_LIMIT.
SCRAMBLED_ID_LIMIT = 10_000_000
_HALF_BITS = 12  # The scramble works on 24-bit numbers, as two 12-bit halves
_HALF_MASK = (1 << _HALF_BITS) - 1


def scrambled_id(scenario_seed: int | str, label: str, number: int) -> int:
    """Map ``number`` (counting from 1) to a different id from 1 to SCRAMBLED_ID_LIMIT.

    The mapping is a permutation keyed by the seed and ``label``. Two numbers
    never share an id, and one number's id doesn't depend on any other. It
    is a four-round Feistel network on 24-bit numbers, repeated until the
    result falls in range ("cycle walking").

    >>> ids = [scrambled_id(1, "site_a", n) for n in range(1, 1001)]
    >>> len(set(ids)) == 1000 and all(1 <= i <= SCRAMBLED_ID_LIMIT for i in ids)
    True
    >>> ids[:3] == [scrambled_id(1, "site_b", n) for n in range(1, 4)]
    False
    """
    if not 1 <= number <= SCRAMBLED_ID_LIMIT:
        msg = f"cannot scramble {number}: must be 1 to {SCRAMBLED_ID_LIMIT}"
        raise ValueError(msg)
    keys = [derive_seed(scenario_seed, label, "scrambled_id", r) for r in range(4)]
    value = number - 1
    while True:
        left, right = value >> _HALF_BITS, value & _HALF_MASK
        for key in keys:
            mixed = derive_seed(key, right) & _HALF_MASK
            left, right = right, left ^ mixed
        value = (left << _HALF_BITS) | right
        if value < SCRAMBLED_ID_LIMIT:
            return value + 1


_NOTHING = object()


def weighted_choice(
    rng: random.Random,
    values: Sequence,
    weights: Sequence[int],
    exclude: object = _NOTHING,
) -> object:
    """Choose a value with probability proportional to its weight.

    ``exclude`` leaves one value out of the draw.
    """
    candidates = [
        i for i, value in enumerate(values) if exclude is _NOTHING or value != exclude
    ]
    ticket = draw_between(rng, 1, sum(weights[i] for i in candidates))
    for i in candidates:
        if ticket <= weights[i]:
            return values[i]
        ticket -= weights[i]
    msg = "unreachable: ticket exceeds total weight"
    raise AssertionError(msg)
