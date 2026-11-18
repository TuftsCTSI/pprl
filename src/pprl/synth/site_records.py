"""Build an individual's true record, and what each site records about them.

The true record holds the individual's date of birth, pooled attributes
(gender, race, ...), name, address and events. A site's record copies the
true record, with these differences:

- Each pooled attribute may be redrawn, with chance ``change_rate``.
- The name and address may vary, as the ``pii`` section says.
- ``at_sites`` may state exactly what the site records.
- The site holds only the events it records.

The date of birth is the same at every site. The site knows the individual
by the site's own person_id (``site_person_id``).

Data flows one way: first the true record, then each site's record from it.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from anyascii import anyascii

from . import pii_source
from .histories import generated_events
from .omop_cdm import PERSON_TABLE, OmopRow
from .scenario import (
    ADDRESS_KEYS,
    Individual,
    Scenario,
    SiteDifferences,
    TextVariation,
)
from .seeded_random import (
    derive_seed,
    draw_index,
    happens,
    scrambled_id,
    seeded_rng,
    shuffled,
    weighted_choice,
)
from .yaml_reading import set_omop_value

if TYPE_CHECKING:
    import random

NAME_KEYS = ("first_name", "last_name")
# What a relative shares with the individual they are related to, besides the
# date of birth that a twin shares. Family at one address often share a surname.
RELATIVE_SHARES = {
    "twin": ("last_name", *ADDRESS_KEYS),
    "family": ADDRESS_KEYS,
    "junior": (*NAME_KEYS, *ADDRESS_KEYS),
}
FAMILY_SURNAME = 0.7


@dataclass
class TrueRecord:
    """An individual's true record, before any site records it."""

    birth_date: date
    attributes: list  # [(person column, value)], one per attribute pool
    pii: dict = field(default_factory=dict)  # Empty when no patient file is written
    events: list = field(default_factory=list)  # Described events, then generated
    person: OmopRow = field(default_factory=dict)  # The true OMOP person row


@dataclass
class SiteRecord:
    """What one site records about an individual."""

    individual: Individual
    person_id: int  # The site's own id for the individual
    birth_date: date
    attributes: list
    differences: SiteDifferences
    pii: dict = field(default_factory=dict)
    events: list = field(default_factory=list)  # The events this site records


def true_record(
    scenario: Scenario, individual: Individual, *, with_pii: bool
) -> TrueRecord:
    """Make the individual's true record.

    The draws from the individual's stream are, in order: a date of birth
    (only when the scenario gives no ``year_of_birth``), then each pooled
    attribute. Names and addresses are drawn from their own seeds, and only
    for fields the scenario doesn't state. Generated events come from each
    module's own streams. A relative shares fields with another individual
    (see ``relation``).
    """
    rng = seeded_rng(scenario.seed, individual.individual_id)
    birth_date = given_birth_date(individual) or pii_source.draw_birth_date(
        rng, *(scenario.birth_years or ())
    )
    attributes = [
        (pool.column, weighted_choice(rng, pool.values, pool.weights))
        for pool in scenario.attribute_pools
    ]
    host, shared = relation(scenario, individual)
    if "dob" in shared:
        birth_date = _shared_birth_date(scenario, host)
    pii = {}
    if with_pii:
        pii = _related_pii(scenario, individual)
        pii.update(individual.pii)
    truth = TrueRecord(birth_date, attributes, pii, list(individual.events))
    truth.person = person_row(scenario, individual, birth_date, attributes)
    truth.events.extend(generated_events(scenario, individual, truth.person))
    return truth


def relation(scenario: Scenario, individual: Individual) -> tuple:
    """Find the earlier individual that a generated ``individual`` is related to.

    Return that individual and what they share, or (None, ()) for most
    individuals.

    The draws come from the individual's own stream: which kind of relative,
    if any (from the scenario's ``relatives``), then which individual, then,
    for family, whether they share a surname. This way a scenario without
    ``relatives`` is unchanged. Adding a relative changes no one else's
    record except those of later relatives.
    """
    generated = scenario.generated
    if not (scenario.relatives and generated):
        return None, ()
    earlier = individual.individual_id - generated[0].individual_id
    if earlier < 1:
        return None, ()
    rng = seeded_rng(scenario.seed, individual.individual_id, "relative")
    draw, edge = rng.random(), 0.0
    for kind, rate in scenario.relatives.items():
        edge += rate
        if draw < edge:
            host = generated[draw_index(rng, earlier)]
            shared = RELATIVE_SHARES[kind]
            if kind == "twin":
                shared = ("dob", *shared)
            elif kind == "family" and happens(rng, FAMILY_SURNAME):
                shared = ("last_name", *shared)
            return host, shared
    return None, ()


def _shared_birth_date(scenario: Scenario, individual: Individual) -> date:
    """Return a generated ``individual``'s date of birth, which a twin shares."""
    host, shared = relation(scenario, individual)
    if "dob" in shared:
        return _shared_birth_date(scenario, host)
    rng = seeded_rng(scenario.seed, individual.individual_id)
    return pii_source.draw_birth_date(rng, *(scenario.birth_years or ()))


def _related_pii(scenario: Scenario, individual: Individual) -> dict:
    """Draw an individual's PII, including what they share as a relative."""
    pii = _drawn_pii(scenario, individual)
    host, shared = relation(scenario, individual)
    if host is not None:
        host_pii = _related_pii(scenario, host)
        pii.update({key: host_pii[key] for key in shared if key != "dob"})
    return pii


def person_row(
    scenario: Scenario,
    individual: Individual,
    birth_date: date,
    attributes: list,
    stated: OmopRow | None = None,
) -> OmopRow:
    """Make an OMOP person row, without its ``person_id``.

    Columns ``stated`` for one site (``at_sites``) take priority, then the
    columns the individual sets. The pooled ``attributes`` fill in the rest,
    and the date of birth columns come from ``birth_date``. Concepts are
    given as ids.
    """
    row = dict(individual.person_columns)
    row.update(stated or {})
    for column, value in attributes:
        if column not in row:
            set_omop_value(row, PERSON_TABLE, column, value, scenario.row_names)
    row["person_source_value"] = individual.person_source_value
    row["year_of_birth"] = birth_date.year
    row["month_of_birth"] = birth_date.month
    row["day_of_birth"] = birth_date.day
    row["birth_datetime"] = datetime.combine(birth_date, time())
    return row


def _drawn_pii(scenario: Scenario, individual: Individual) -> dict:
    drawn = {}
    if any(key not in individual.pii for key in NAME_KEYS):
        name_seed = derive_seed(scenario.seed, individual.individual_id, "name")
        drawn.update(zip(NAME_KEYS, pii_source.draw_name(name_seed), strict=True))
    if any(key not in individual.pii for key in ADDRESS_KEYS):
        address_seed = derive_seed(scenario.seed, individual.individual_id, "address")
        drawn.update(
            zip(ADDRESS_KEYS, pii_source.draw_address(address_seed), strict=True)
        )
    return drawn


def site_record(
    scenario: Scenario, individual: Individual, site: str, truth: TrueRecord
) -> SiteRecord:
    """Make the individual's record at ``site``.

    The draws from this individual's stream at this site are, in order:

    1. each pooled attribute: whether it is redrawn, then its new value
    2. the first name, then the last name (see ``vary_text``)
    3. whether the address changes altogether, then the city, state and ZIP
       code (see ``vary_text``)

    Every decision is drawn whether or not it applies, so stating a field in
    the YAML never changes how any other field varies.
    """
    rng = seeded_rng(scenario.seed, individual.individual_id, site)
    attributes = []
    for pool, (column, true_value) in zip(
        scenario.attribute_pools, truth.attributes, strict=True
    ):
        value = true_value
        if happens(rng, pool.change_rate):
            excluded = {"exclude": true_value} if pool.change_rate >= 1.0 else {}
            value = weighted_choice(rng, pool.values, pool.weights, **excluded)
        attributes.append((column, value))
    differences = individual.at_sites.get(site, SiteDifferences())
    pii = {}
    if truth.pii:
        pii = _site_pii(rng, scenario, individual, site, truth.pii)
        pii.update(differences.pii)
    events = [
        event for event in truth.events if event.sites is None or site in event.sites
    ]
    return SiteRecord(
        individual,
        site_person_id(scenario, individual, site),
        truth.birth_date,
        attributes,
        differences,
        pii,
        events,
    )


def site_person_id(scenario: Scenario, individual: Individual, site: str) -> int:
    """Return the individual's person_id at ``site``.

    With ``person_ids: per_site``, each site scrambles the individual id
    into its own id, so ids don't correspond between sites. Otherwise it is
    the individual id at every site.
    """
    if scenario.person_ids == "per_site":
        return scrambled_id(scenario.seed, site, individual.individual_id)
    return individual.individual_id


def _site_pii(
    rng: random.Random,
    scenario: Scenario,
    individual: Individual,
    site: str,
    true_pii: dict,
) -> dict:
    variation = scenario.pii_variation
    pii = {key: vary_text(rng, true_pii[key], variation.name) for key in NAME_KEYS}
    address_changed = happens(rng, variation.address_change_rate)
    pii.update(
        {
            key: vary_text(
                rng, true_pii[key], variation.address, fixed_format=key in FIXED_FORMAT
            )
            for key in ADDRESS_KEYS
        }
    )
    if address_changed:
        new_seed = derive_seed(scenario.seed, individual.individual_id, site, "address")
        pii.update(zip(ADDRESS_KEYS, pii_source.draw_address(new_seed), strict=True))
    # PII that the scenario states is used as written. It never varies.
    pii.update(individual.pii)
    return pii


# ---- Date of birth -----------------------------------------------------------


def given_birth_date(individual: Individual) -> date | None:
    """Return the date from ``year_of_birth`` (month and day are 1), or None."""
    columns = individual.person_columns
    year = columns.get("year_of_birth")
    if year is None:
        return None
    return date(
        year, columns.get("month_of_birth") or 1, columns.get("day_of_birth") or 1
    )


# ---- Variation in text fields ------------------------------------------------


def vary_text(
    rng: random.Random,
    text: str | None,
    variation: TextVariation,
    *,
    fixed_format: bool = False,
) -> str | None:
    """Make a site's copy of one text field (a first name, a city, ...).

    The draws are, in order: whether there are typos (then the typos),
    whether the case changes (then upper, lower or title case), whether the
    field is left empty, and whether accented letters become plain ASCII.
    With ``fixed_format`` (a state or ZIP code), the value keeps the format
    that pprl hash requires. Typos never delete a character (see
    ``add_typos``), and the case never changes, though it is still drawn.
    """
    if happens(rng, variation.typos.rate) and text:
        text = add_typos(rng, text, variation.typos.max_edits, keep_length=fixed_format)
    if happens(rng, variation.case_change):
        case = draw_index(rng, 3)
        if text and not fixed_format:
            text = (text.upper(), text.lower(), text.title())[case]
    if happens(rng, variation.missing):
        text = None
    if happens(rng, variation.transliteration) and text:
        text = anyascii(text)
    return text


_TYPO_OPERATIONS = ("delete", "swap", "replace")
# Fields with a fixed format. Typos in them keep the
# format.
FIXED_FORMAT = ("state", "zip")
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER = _UPPER.lower()
_DIGITS = "0123456789"


def add_typos(
    rng: random.Random, text: str, edits: int, *, keep_length: bool = False
) -> str:
    """Make ``edits`` typos, each at a letter or digit.

    Each typo first draws an operation, then a position. The operation is
    one of: delete the character, swap it with the next one, or replace it
    with another character of the same kind (an uppercase letter, a
    lowercase letter, or a digit). With ``keep_length``, the operation is
    only a swap or a replacement, so a 5-digit ZIP code stays 5 digits.

    >>> import random
    >>> add_typos(random.Random(1), "Surname", 1) != "Surname"
    True
    """
    for _ in range(edits):
        positions = [i for i, character in enumerate(text) if character.isalnum()]
        if not positions:
            break
        operations = _TYPO_OPERATIONS[1:] if keep_length else _TYPO_OPERATIONS
        operation = operations[draw_index(rng, len(operations))]
        i = positions[draw_index(rng, len(positions))]
        if operation == "delete":
            text = text[:i] + text[i + 1 :]
        elif operation == "swap":
            j = i + 1 if i + 1 < len(text) else i - 1
            if j >= 0:
                characters = list(text)
                characters[i], characters[j] = characters[j], characters[i]
                text = "".join(characters)
        else:
            text = text[:i] + _replacement(rng, text[i]) + text[i + 1 :]
    return text


def _replacement(rng: random.Random, character: str) -> str:
    if character.isdigit():
        alphabet = _DIGITS
    elif character.isupper():
        alphabet = _UPPER
    else:
        alphabet = _LOWER
    choices = [c for c in alphabet if c != character]
    return choices[draw_index(rng, len(choices))]


def shuffled_rows(rows: list, scenario: Scenario, site: str) -> list:
    """Shuffle rows, using a stream for this site's patient file."""
    return shuffled(seeded_rng(scenario.seed, site, "shuffle_rows"), rows)
