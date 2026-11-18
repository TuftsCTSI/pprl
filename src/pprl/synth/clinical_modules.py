"""Define the clinical modules that draw the events of generated individuals.

Each module draws one kind of event in an individual's history, such as a
hypertension diagnosis or mammograms. Concepts share a module only when they
are drawn together: the two halves of a blood pressure reading, the scores
of one questionnaire, the second dose of a vaccine after the first. Modules
are not a clinical classification, and they don't know about each other.

A module is roughly coherent. It follows a few simple rules (a plausible
age range, a systolic pressure above the diastolic), but its ranges are
wider than any guideline, so values fall on both sides of any threshold.
The ``scatter`` module follows no rules at all: it draws any event, for
anyone, on any date.

A module draws *event groups*: events recorded together on one date, such
as the systolic and diastolic halves of one blood pressure reading. Which
sites record each group is decided later, in the histories module. An event
group keeps the function that drew it, so a site can record a redrawn copy:
the same kind of event, earlier or later in the same month, with new values.

Events are written as YAML-style rows (``{"concept_id": "systolic_bp",
"date": ..., "value_as_number": 139}``) and read like any event in a
scenario, so every value goes through ``set_omop_value``. The concepts that
events name are in ``MODULE_CONCEPTS``, which synth adds to any scenario
that uses modules. Their names and ids match the Boston example scenario.

This module knows nothing about the scenario. It depends only on
``seeded_random``.
"""

import calendar
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, TypedDict, Unpack

from .seeded_random import (
    draw_between,
    draw_choice,
    draw_index,
    draw_tenths,
    happens,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# ---- Vocabulary --------------------------------------------------------------

MODULE_CONCEPTS = {
    # Person
    "MALE": 8507,
    # Visits
    "outpatient": 9202,
    "inpatient": 9201,
    # Conditions
    "hypertension": 320128,
    "type_2_diabetes": 201826,
    "depression": 4102973,
    "anxiety": 4217945,
    "schizophrenia": 432598,
    "bipolar": 4031928,
    "substance_abuse": 4279309,
    "sud_alcohol": 433753,
    "opioid_sud": 4032799,
    "vision_disability": 372906,
    "hearing_disability": 443608,
    "autism_spectrum": 43020503,
    "intellectual_disability": 35207238,
    "advanced_illness": 4004279,
    "frailty_dx": 438567,
    "esrd": 193782,
    "pregnancy": 4299535,
    # Measurements
    "systolic_bp": 3004249,
    "diastolic_bp": 3012888,
    "hba1c": 3004410,
    "bmi": 3038553,
    "bmi_percentile": 40490382,
    "fobt": 3027299,
    "sdna_fit": 46236059,
    "percent": 8554,
    # Procedures
    "mammography": 3016169,
    "mastectomy": 2006395,
    "colonoscopy": 37163368,
    "sigmoidoscopy": 4262589,
    "ct_colonography": 40763569,
    "colectomy_proc": 4096461,
    "kidney_transplant_proc": 4322471,
    # Observations
    "routine_exam": 4085802,
    "food_security_q1": 36304041,
    "food_security_q2": 36306143,
    "housing_stability": 42869557,
    "homelessness": 4139934,
    "housing_lack": 4144274,
    "psc17_internalization": 2000000095,
    "psc17_externalization": 2000000096,
    "psc17_attention": 2000000097,
    "psc17_total": 2000000094,
    "tobacco_status": 4275495,
    "frailty_encounter": 2514578,
    "frailty_symptom": 4008159,
    "hospice_enc": 4140947,
    "acute_inpatient_admission": 4138051,
    # Answers
    "never_true": 36309869,
    "sometimes_true": 45877955,
    # Local concepts (2,000,000,000 and up, the range OMOP reserves for them)
    "steady_place": 2000000101,
    "worried_about_losing": 2000000102,
    "yes_homeless": 2000000103,
    "tobacco_user": 4005823,
    "tobacco_nonuser": 4022662,
    "tobacco_unknown_val": 45884037,
    # Drugs
    "covid_pfizer": 702866,
    "mmr_vaccine": 40213183,
}

# Direct (ancestor, descendant) pairs, by concept name.
MODULE_CONCEPT_ANCESTORS = [
    ("substance_abuse", "sud_alcohol"),
    ("substance_abuse", "opioid_sud"),
]

# A visit recorded without a valid visit type.
NO_VISIT_TYPE = 0


# ---- Events ------------------------------------------------------------------

# A function that draws one event group on a date, returning
# [(YAML table name, YAML-style row)].
Maker = Callable[[random.Random, date], list]


def diagnosis(concept: str, resolves: float = 0.0) -> Maker:
    """Draw a condition. With chance ``resolves``, it ends 1 to 24 months later."""

    def make(rng: random.Random, day: date) -> list:
        row = {"concept_id": concept, "start_date": day}
        if happens(rng, resolves):
            row["end_date"] = day + timedelta(days=draw_between(rng, 30, 730))
        return [("condition", row)]

    return make


def procedure(concept: str) -> Maker:
    """Make a procedure of ``concept``."""
    return lambda _rng, day: [("procedure", {"concept_id": concept, "date": day})]


def observation(concept: str) -> Maker:
    """Make an observation of ``concept``."""
    return lambda _rng, day: [("observation", {"concept_id": concept, "date": day})]


def measurement(
    concept: str,
    low: float,
    high: float,
    *,
    tenths: bool = False,
    unit: str | None = None,
    no_value: float = 0.0,
) -> Maker:
    """Draw a measurement with a value from ``low`` to ``high``.

    The value is a whole number or in tenths. With chance ``no_value``, the
    value is empty.
    """

    def make(rng: random.Random, day: date) -> list:
        if tenths:
            value = draw_tenths(rng, low, high)
        else:  # Whole numbers, as every such measurement gives
            value = draw_between(rng, int(low), int(high))
        row = {"concept_id": concept, "date": day, "value_as_number": value}
        if happens(rng, no_value):
            row["value_as_number"] = None
        if unit is not None:
            row["unit_concept_id"] = unit
        return [("measurement", row)]

    return make


def answer(concept: str, answers: Sequence[tuple[str | None, str]]) -> Maker:
    """Draw an observation answered with one of ``answers``.

    Each answer is (concept or None, text).
    """

    def make(rng: random.Random, day: date) -> list:
        value_concept, text = draw_choice(rng, answers)
        row = {"concept_id": concept, "date": day, "value_as_string": text}
        if value_concept is not None:
            row["value_as_concept_id"] = value_concept
        return [("observation", row)]

    return make


def vaccine(concept: str, lot_rate: float = 0.9) -> Maker:
    """Draw a vaccine dose, with a lot number with chance ``lot_rate``."""

    def make(rng: random.Random, day: date) -> list:
        row = {"concept_id": concept, "start_date": day}
        lot = f"LOT{draw_between(rng, 1000, 9999)}"
        if happens(rng, lot_rate):
            row["lot_number"] = lot
        return [("drug", row)]

    return make


def blood_pressure(rng: random.Random, day: date) -> list:
    """Draw systolic (90-200) and diastolic (40-130, below the systolic) pressures."""
    systolic = draw_between(rng, 90, 200)
    diastolic = draw_between(rng, 40, min(130, systolic - 10))
    return [
        (
            "measurement",
            {"concept_id": "systolic_bp", "date": day, "value_as_number": systolic},
        ),
        (
            "measurement",
            {"concept_id": "diastolic_bp", "date": day, "value_as_number": diastolic},
        ),
    ]


PSC17_SCALES = (
    ("psc17_internalization", 10),
    ("psc17_attention", 10),
    ("psc17_externalization", 14),
)


def psc17_scores(rng: random.Random, day: date) -> list:
    """Draw Pediatric Symptom Checklist scores: each scale, then their total.

    Each scale is left out with chance 0.1, and the total with chance 0.2.
    """
    events = []
    total = 0
    for concept, highest in PSC17_SCALES:
        score = draw_between(rng, 0, highest)
        total += score
        if not happens(rng, 0.1):
            events.append(_scored(concept, day, score))
    if not happens(rng, 0.2):
        events.append(_scored("psc17_total", day, total))
    return events


def _scored(concept: str, day: date, score: int) -> tuple[str, dict]:
    return (
        "observation",
        {"concept_id": concept, "date": day, "value_as_number": score},
    )


FOOD_ANSWERS = [
    ("never_true", "Never true"),
    ("sometimes_true", "Sometimes true"),
    (None, "Patient declined to answer"),
]
HOUSING_ANSWERS = [
    ("steady_place", "I have a steady place to live"),
    (
        "worried_about_losing",
        "I have a place to live today, but I am worried about losing it",
    ),
    (None, "Prefer not to say"),
]
YES_HOMELESS = [("yes_homeless", "Yes")]


TOBACCO_ANSWERS = [
    "tobacco_user",
    "tobacco_nonuser",
    "tobacco_nonuser",
    "tobacco_unknown_val",
]


def tobacco_status(rng: random.Random, day: date) -> list:
    """Draw a tobacco status: user, non-user (twice as likely), or unclassified."""
    value_concept = draw_choice(rng, TOBACCO_ANSWERS)
    row = {
        "concept_id": "tobacco_status",
        "date": day,
        "value_as_concept_id": value_concept,
    }
    return [("observation", row)]


def pregnancy(rng: random.Random, day: date) -> list:
    """Draw a pregnancy ending 200 to 290 days after it is recorded."""
    end = day + timedelta(days=draw_between(rng, 200, 290))
    return [
        ("condition", {"concept_id": "pregnancy", "start_date": day, "end_date": end})
    ]


MMR = vaccine("mmr_vaccine")
COVID_VACCINE = vaccine("covid_pfizer")
BMI = measurement("bmi", 12.0, 60.0, tenths=True, no_value=0.05)
BMI_PERCENTILE = measurement("bmi_percentile", 1, 99)
ROUTINE_EXAM = observation("routine_exam")


def food_security_screen(rng: random.Random, day: date) -> list:
    """Draw both food security questions, each asked with chance 0.8."""
    return _asked(
        rng,
        day,
        [
            (0.8, "food_security_q1", FOOD_ANSWERS),
            (0.8, "food_security_q2", FOOD_ANSWERS),
        ],
    )


def housing_screen(rng: random.Random, day: date) -> list:
    """Draw answers about housing stability and homelessness.

    Housing stability is asked with chance 0.8. The two homelessness
    questions are each asked with chance 0.1.
    """
    return _asked(
        rng,
        day,
        [
            (0.8, "housing_stability", HOUSING_ANSWERS),
            (0.1, "homelessness", YES_HOMELESS),
            (0.1, "housing_lack", YES_HOMELESS),
        ],
    )


def _asked(
    rng: random.Random, day: date, questions: Iterable[tuple[float, str, Sequence]]
) -> list:
    events = []
    for rate, concept, answers in questions:
        asked = happens(rng, rate)
        answered = answer(concept, answers)(rng, day)
        if asked:
            events.extend(answered)
    return events


# Every kind of event a module draws. `scatter` draws from these. Routine
# exams appear here without the visit columns that the `routine_exam` module
# adds.
EVENT_KINDS = {
    "hypertension": diagnosis("hypertension"),
    "blood_pressure": blood_pressure,
    "type_2_diabetes": diagnosis("type_2_diabetes"),
    "hba1c": measurement("hba1c", 4.0, 14.0, tenths=True, unit="percent"),
    "bmi": BMI,
    "bmi_percentile": BMI_PERCENTILE,
    "psc17": psc17_scores,
    "food_security_screen": food_security_screen,
    "housing_screen": housing_screen,
    "tobacco_status": tobacco_status,
    "mmr_vaccine": MMR,
    "covid_vaccine": COVID_VACCINE,
    "depression": diagnosis("depression", resolves=0.3),
    "anxiety": diagnosis("anxiety", resolves=0.3),
    "schizophrenia": diagnosis("schizophrenia", resolves=0.3),
    "bipolar": diagnosis("bipolar", resolves=0.3),
    "alcohol_use_disorder": diagnosis("sud_alcohol", resolves=0.3),
    "opioid_use_disorder": diagnosis("opioid_sud", resolves=0.3),
    "substance_abuse": diagnosis("substance_abuse", resolves=0.3),
    "vision_disability": diagnosis("vision_disability", resolves=0.05),
    "hearing_disability": diagnosis("hearing_disability", resolves=0.05),
    "autism_spectrum": diagnosis("autism_spectrum", resolves=0.05),
    "intellectual_disability": diagnosis("intellectual_disability", resolves=0.05),
    "mammography": procedure("mammography"),
    "mastectomy": procedure("mastectomy"),
    "fobt": measurement("fobt", 0, 1),
    "fit_dna": measurement("sdna_fit", 0, 1),
    "colonoscopy": procedure("colonoscopy"),
    "sigmoidoscopy": procedure("sigmoidoscopy"),
    "ct_colonography": procedure("ct_colonography"),
    "colectomy": procedure("colectomy_proc"),
    "advanced_illness": diagnosis("advanced_illness"),
    "frailty_diagnosis": diagnosis("frailty_dx"),
    "frailty_encounter": observation("frailty_encounter"),
    "frailty_symptom": observation("frailty_symptom"),
    "hospice": observation("hospice_enc"),
    "acute_inpatient_admission": observation("acute_inpatient_admission"),
    "esrd": diagnosis("esrd"),
    "kidney_transplant": procedure("kidney_transplant_proc"),
    "pregnancy": pregnancy,
    "routine_exam": ROUTINE_EXAM,
}


# ---- One individual's history from one module --------------------------------


@dataclass
class EventGroup:
    """Events recorded together on one date, and the function that drew them."""

    module: str
    make: Maker
    day: date
    events: list
    # The visit's columns, for a routine exam. A site's copy of a routine exam
    # is never redrawn, so two routine exams never share a date.
    visit: dict | None = None


class Span(TypedDict, total=False):
    """Where in an individual's life a module's events fall (see History.span)."""

    lookback_years: int
    min_age: float
    max_age: float | None


@dataclass
class History:
    """What a module knows about an individual, and the event groups it draws."""

    module: str
    rng: random.Random
    person: dict  # The individual's true OMOP person row, with concept ids
    first_year: int
    last_year: int
    care_sites: list  # Care site names where a routine exam can take place
    groups: list = field(default_factory=list)

    @property
    def birth_date(self) -> date:
        """Return the individual's date of birth."""
        return self.person["birth_datetime"].date()

    def has(self, column: str, concept: str) -> bool:
        """Tell whether the person row's ``column`` holds the concept ``concept``."""
        return self.person.get(column) == MODULE_CONCEPTS[concept]

    @property
    def window_start(self) -> date:
        """Return the first day of the scenario's window."""
        return date(self.first_year, 1, 1)

    @property
    def window_end(self) -> date:
        """Return the last day of the scenario's window."""
        return date(self.last_year, 12, 31)

    def at_age(self, years: float) -> date:
        """Return the date the individual reaches age ``years`` (in whole months)."""
        months = round(years * 12)
        year = self.birth_date.year + (self.birth_date.month - 1 + months) // 12
        month = (self.birth_date.month - 1 + months) % 12 + 1
        day = min(self.birth_date.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def span(
        self,
        lookback_years: int = 0,
        min_age: float = 0,
        max_age: float | None = None,
    ) -> tuple[date, date]:
        """Return the span of dates for a module's events.

        The span runs from ``lookback_years`` before the window to the end of
        the window. It is limited to the time from the day the individual
        reaches ``min_age`` until they reach ``max_age + 1``. So
        ``max_age=85`` includes every 85-year-old.
        """
        start = max(date(self.first_year - lookback_years, 1, 1), self.at_age(min_age))
        end = self.window_end
        if max_age is not None:
            end = min(end, self.at_age(max_age + 1) - timedelta(days=1))
        return start, end

    def add(self, make: Maker, day: date, visit: dict | None = None) -> None:
        """Add an event group that ``make`` draws on ``day``, at ``visit`` if given."""
        self.groups.append(
            EventGroup(self.module, make, day, make(self.rng, day), visit)
        )

    def once(self, make: Maker, **span: Unpack[Span]) -> date | None:
        """Draw one event group on a day in the span (see ``span``), if it has days."""
        start, end = self.span(**span)
        day = _day_between(self.rng, start, end)
        if day is not None:
            self.add(make, day)
        return day

    def days_each_year(self, low: int, high: int, **span: Unpack[Span]) -> list:
        """Draw ``low`` to ``high`` different days in each year of the span."""
        start, end = self.span(**span)
        days = []
        for year in range(start.year, end.year + 1):
            first, last = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
            count = draw_between(self.rng, low, high)
            days.extend(_distinct_days(self.rng, first, last, count))
        return days

    def yearly(self, make: Maker, low: int, high: int, **span: Unpack[Span]) -> list:
        """Draw ``low`` to ``high`` event groups a year, on different days."""
        days = self.days_each_year(low, high, **span)
        for day in days:
            self.add(make, day)
        return days


def _day_between(rng: random.Random, start: date, end: date) -> date | None:
    """Draw a uniform day from ``start`` to ``end``, or None (after a draw) if empty."""
    offset = draw_index(rng, max((end - start).days + 1, 1))
    return start + timedelta(days=offset) if start <= end else None


def _distinct_days(rng: random.Random, start: date, end: date, count: int) -> list:
    """Draw up to ``count`` different days from ``start`` to ``end``, in order."""
    if start > end:
        return []
    all_days = (end - start).days + 1
    offsets = set()
    for _ in range(min(count, all_days)):
        offset = draw_index(rng, all_days)
        while offset in offsets:
            offset = (offset + 1) % all_days
        offsets.add(offset)
    return [start + timedelta(days=offset) for offset in sorted(offsets)]


def day_in_same_month(rng: random.Random, day: date) -> date:
    """Draw a uniform day in the month of ``day``."""
    last = calendar.monthrange(day.year, day.month)[1]
    return date(day.year, day.month, draw_between(rng, 1, last))


# ---- The modules -------------------------------------------------------------
#
# Most modules draw one kind of event, using one of three patterns:
# - ``once``: once, on a day in a span.
# - ``yearly``: on some days each year.
# - ``episodes``: as a condition that can resolve and recur.
# Each pattern takes the span its events fall in (see ``History.span``). With
# ``skip_if`` ({person column: concept name}), a pattern skips individuals
# whose person row holds any of those concepts. The other modules are written
# out in full.

Module = Callable[[History], None]


def _skipped(h: History, skip_if: dict | None) -> bool:
    return any(h.has(column, concept) for column, concept in (skip_if or {}).items())


def once(kind: str, skip_if: dict | None = None, **span: Unpack[Span]) -> Module:
    """Make a module that draws one event on a day in the span."""

    def module(h: History) -> None:
        if not _skipped(h, skip_if):
            h.once(EVENT_KINDS[kind], **span)

    return module


def yearly(
    kind: str, low: int, high: int, skip_if: dict | None = None, **span: Unpack[Span]
) -> Module:
    """Make a module that draws ``low`` to ``high`` events a year, on different days."""

    def module(h: History) -> None:
        if not _skipped(h, skip_if):
            h.yearly(EVENT_KINDS[kind], low, high, **span)

    return module


def episodes(kind: str, **span: Unpack[Span]) -> Module:
    """Make a module that draws a condition in the span, perhaps more than once.

    Some conditions resolve (see ``EVENT_KINDS``). With chance 0.1, a second
    episode starts a year or more after the first.
    """

    def module(h: History) -> None:
        make = EVENT_KINDS[kind]
        first = h.once(make, **span)
        recurs = happens(h.rng, 0.1)
        year_later = (first or h.window_start) + timedelta(days=365)
        later = _day_between(h.rng, year_later, h.window_end)
        if first is not None and recurs and later is not None:
            h.add(make, later)

    return module


def blood_pressure_readings(h: History) -> None:
    """Draw 0-4 readings a year from age 5, each sometimes repeated the same day."""
    for day in h.yearly(blood_pressure, 0, 4, min_age=5):
        if happens(h.rng, 0.1):
            h.add(blood_pressure, day)


# BMI is recorded as a percentile for children, and as a BMI from these ages.
EARLIEST_ADULT_AGE, ADULT_AGE = 18, 20


def body_mass_index(h: History) -> None:
    """Draw 0-3 BMI readings a year from age 2.

    Under 20, a reading is a BMI percentile (1-99). From 18, it is a BMI
    (12.0-60.0), sometimes without a value.
    """
    for day in h.days_each_year(0, 3, min_age=2):
        age = (day - h.birth_date).days / 365.25
        adult = age >= ADULT_AGE or (age >= EARLIEST_ADULT_AGE and happens(h.rng, 0.5))
        h.add(BMI if adult else BMI_PERCENTILE, day)


def mmr_vaccine(h: History) -> None:
    """Draw a first MMR dose at 9-24 months, and perhaps a second.

    With chance 0.5, a second dose follows 1-12 months later.
    """
    first = h.once(MMR, lookback_years=5, min_age=0.75, max_age=1)
    gap = timedelta(days=draw_between(h.rng, 28, 365))
    has_second = happens(h.rng, 0.5)
    if first is not None and has_second and first + gap <= h.window_end:
        h.add(MMR, first + gap)


def covid_vaccine(h: History) -> None:
    """Draw 0-3 doses on different days, from 2021 and from 6 months old."""
    start, end = h.span(lookback_years=h.first_year - 2021, min_age=0.5)
    for day in _distinct_days(h.rng, start, end, draw_between(h.rng, 0, 3)):
        h.add(COVID_VACCINE, day)


# Outpatient 8 times in 10; inpatient once, and without a valid type once.
VISIT_TYPES = ["outpatient"] * 8 + ["inpatient", NO_VISIT_TYPE]


def routine_exam(h: History) -> None:
    """Draw 0-3 routine exams a year, from 4 years before the window.

    This is the only module that adds visit columns. Each exam is its own
    visit: outpatient, inpatient, or without a valid visit type. It takes
    place at one of the scenario's care sites, or at none.
    """
    for day in h.days_each_year(0, 3, lookback_years=4):
        visit = {"concept_id": draw_choice(h.rng, VISIT_TYPES)}
        care_site = draw_choice(h.rng, [None, *h.care_sites])
        if care_site is not None:
            visit["care_site_id"] = care_site
        h.add(ROUTINE_EXAM, day, visit)


def scatter(h: History) -> None:
    """Scatter 1-8 events of any kind, on any date.

    Dates start 10 years before the window, whatever the individual's age or
    sex.
    """
    kinds = list(EVENT_KINDS.values())
    start = date(h.first_year - 10, 1, 1)
    for _ in range(draw_between(h.rng, 1, 8)):
        make = draw_choice(h.rng, kinds)
        day = _day_between(h.rng, start, h.window_end)
        if day is None:  # Never: the window ends after start.
            raise AssertionError
        h.add(make, day)


MODULES = {
    # Concepts drawn together, in one event group or one after another
    "routine_exam": routine_exam,
    "blood_pressure": blood_pressure_readings,
    "body_mass_index": body_mass_index,
    "psc17": yearly("psc17", 0, 2, min_age=3, max_age=18),
    "food_security_screen": yearly("food_security_screen", 0, 2),
    "housing_screen": yearly("housing_screen", 0, 2),
    "mmr_vaccine": mmr_vaccine,
    # One concept each
    "hypertension": once("hypertension", lookback_years=10, min_age=12),
    "type_2_diabetes": once("type_2_diabetes", lookback_years=10, min_age=10),
    "hba1c": yearly("hba1c", 0, 3, min_age=10),
    "tobacco_status": yearly("tobacco_status", 0, 2, min_age=12),
    "covid_vaccine": covid_vaccine,
    **{
        kind: episodes(kind, lookback_years=10, min_age=10)
        for kind in (
            "depression",
            "anxiety",
            "schizophrenia",
            "bipolar",
            "alcohol_use_disorder",
            "opioid_use_disorder",
            "substance_abuse",
        )
    },
    "vision_disability": once("vision_disability", lookback_years=30),
    "hearing_disability": once("hearing_disability", lookback_years=30),
    "autism_spectrum": once("autism_spectrum", lookback_years=30, min_age=1, max_age=8),
    "intellectual_disability": once(
        "intellectual_disability", lookback_years=30, min_age=1, max_age=12
    ),
    "mammography": yearly(
        "mammography",
        0,
        1,
        skip_if={"gender_concept_id": "MALE"},
        lookback_years=4,
        min_age=35,
        max_age=85,
    ),
    "mastectomy": once(
        "mastectomy",
        skip_if={"gender_concept_id": "MALE"},
        lookback_years=20,
        min_age=30,
    ),
    "fobt": yearly("fobt", 0, 1, lookback_years=2, min_age=40, max_age=85),
    "fit_dna": once("fit_dna", lookback_years=5, min_age=40, max_age=85),
    "colonoscopy": once("colonoscopy", lookback_years=12, min_age=40, max_age=85),
    "sigmoidoscopy": once("sigmoidoscopy", lookback_years=7, min_age=40, max_age=85),
    "ct_colonography": once(
        "ct_colonography", lookback_years=7, min_age=40, max_age=85
    ),
    "colectomy": once("colectomy", lookback_years=20, min_age=30),
    "advanced_illness": once("advanced_illness", lookback_years=3, min_age=50),
    "frailty_diagnosis": once("frailty_diagnosis", lookback_years=3, min_age=60),
    "frailty_encounter": once("frailty_encounter", lookback_years=3, min_age=60),
    "frailty_symptom": once("frailty_symptom", lookback_years=3, min_age=60),
    "hospice": once("hospice", lookback_years=3, min_age=40),
    "acute_inpatient_admission": once("acute_inpatient_admission", lookback_years=3),
    "esrd": once("esrd", lookback_years=3, min_age=30),
    "kidney_transplant": once("kidney_transplant", lookback_years=3, min_age=18),
    "pregnancy": once(
        "pregnancy",
        skip_if={"gender_concept_id": "MALE"},
        lookback_years=1,
        min_age=15,
        max_age=45,
    ),
    # No rules
    "scatter": scatter,
}
