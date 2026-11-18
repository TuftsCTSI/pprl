"""Draw identifying information: dates of birth, names and addresses.

Everything synth draws about who an individual is goes through the three
functions below. Another source can replace this module, as long as it
provides the same three functions with the same meaning:

- ``draw_birth_date(rng, first_year, last_year)``: a date of birth, drawn
  from the record's stream
- ``draw_name(seed)``: a first and last name
- ``draw_address(seed)``: a city, state and ZIP code

Names and addresses come from Mimesis (US English locale). Each draw uses a
new provider seeded from the scenario seed,
so nothing depends on global random state. The output is fixed by the
pinned Mimesis version.
"""

import calendar
from datetime import date
from typing import TYPE_CHECKING

from mimesis import Address, Person
from mimesis.locales import Locale

from .seeded_random import draw_between

if TYPE_CHECKING:
    import random

# Unless the scenario gives `birth_years`, drawn dates of birth fall within
# these years, inclusive.
FIRST_BIRTH_YEAR = 1930
LAST_BIRTH_YEAR = 2010


def draw_birth_date(
    rng: random.Random,
    first_year: int = FIRST_BIRTH_YEAR,
    last_year: int = LAST_BIRTH_YEAR,
) -> date:
    """Draw a date of birth: the year, then the month, then the day."""
    year = draw_between(rng, first_year, last_year)
    month = draw_between(rng, 1, 12)
    day = draw_between(rng, 1, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def draw_name(seed: int) -> tuple[str, str]:
    """Draw a first and last name."""
    person = Person(Locale.EN, seed=seed)
    return person.first_name(), person.last_name()


def draw_address(seed: int) -> tuple[str, str, str]:
    """Draw a city, a two-letter state abbreviation and a five-digit ZIP code.

    The three are drawn independently, so they don't agree with each other.
    """
    address = Address(Locale.EN, seed=seed)
    return address.city(), address.state(abbr=True), address.zip_code()
