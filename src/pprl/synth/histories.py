"""Generate an individual's history, and decide which sites record each part.

Each clinical module the individual has draws groups of events. Each event
group is then recorded at some of the sites that can record it, which are
the individual's sites whose ``site_coverage`` includes the module. The
``event_sharing`` chances decide whether that is one site, several, or all
of them. One of those sites gets the event group as drawn. Each other site
records an identical copy, or, with chance ``same_month``, a redrawn copy
with new values on a day in the same month. Routine exams are never
redrawn, so each one stays a single visit on its own date.

Each module has two random streams per individual. One draws the history
(after a first draw that decides whether the individual has the module at
all). The other decides which sites record it. So changing one module, or
how events are shared, never changes what another module draws.
"""

from typing import TYPE_CHECKING

from .clinical_modules import (
    MODULES,
    EventGroup,
    History,
    day_in_same_month,
)
from .omop_cdm import known_table
from .scenario import Individual, Scenario, read_event
from .seeded_random import (
    draw_between,
    first_of_rates,
    happens,
    seeded_rng,
    shuffled,
)

if TYPE_CHECKING:
    import random


def generated_events(scenario: Scenario, individual: Individual, person: dict) -> list:
    """Generate the events of each clinical module the individual has, with sites.

    Each module sees the individual's true OMOP person row, ``person``.
    """
    events = []
    for use in individual.modules:
        rng = seeded_rng(scenario.seed, individual.individual_id, "module", use.name)
        if not happens(rng, use.prevalence):
            continue
        years = scenario.measurement_years
        if years is None:  # Never: a scenario with modules must give years.
            raise AssertionError
        history = History(
            module=use.name,
            rng=rng,
            person=person,
            first_year=years[0],
            last_year=years[1],
            care_sites=list(scenario.row_names.get("care_site", {})),
        )
        MODULES[use.name](history)
        sharing_rng = seeded_rng(
            scenario.seed, individual.individual_id, "sharing", use.name
        )
        for group in history.groups:
            for sites, rows, visit in recorded_copies(
                sharing_rng, scenario, individual, group
            ):
                events.extend(_clinical_events(scenario, rows, visit, sites))
    return events


def recorded_copies(
    rng: random.Random, scenario: Scenario, individual: Individual, group: EventGroup
) -> list:
    """Decide which sites record ``group``, and what each one records.

    Return ``[(sites, rows, visit)]``. The first entry is the group as drawn,
    at one or more sites. Then there is one entry per site that records a
    redrawn copy.

    The draws are, in order: how many sites record the group, the order of
    the sites (a shuffle), and then, for each site after the first, whether
    its copy is redrawn, the copy's day, and its values. Every draw is made
    whether or not it is used, so the stream stays in step.
    """
    sharing = scenario.event_sharing
    can_record = [
        site for site in individual.sites if sharing.covers(site, group.module)
    ]
    how_many = first_of_rates(rng, sharing.rates)
    recipients = shuffled(rng, can_record)[: _site_count(rng, how_many, can_record)]
    if not recipients:
        return []
    same_copy = [recipients[0]]
    redrawn = []
    for site in recipients[1:]:
        is_redrawn = happens(rng, sharing.same_month)
        day = day_in_same_month(rng, group.day)
        rows = group.make(rng, day)
        if is_redrawn and group.visit is None:
            redrawn.append(([site], rows, None))
        else:
            same_copy.append(site)
    in_site_order = [site for site in individual.sites if site in same_copy]
    return [(in_site_order, group.events, group.visit), *redrawn]


# "Several" sites (from 2 to all but one) needs at least this many sites.
_FEWEST_FOR_SEVERAL = 3


def _site_count(rng: random.Random, how_many: int, sites: list) -> int:
    """Draw how many sites record a group: one, several, or all.

    "Several" means from 2 to all but one, and needs at least 3 sites.
    """
    several = draw_between(rng, 2, max(len(sites) - 1, 2))
    if how_many == 0:
        return 1
    if how_many == 1 and len(sites) >= _FEWEST_FOR_SEVERAL:
        return several
    return len(sites)


def _clinical_events(
    scenario: Scenario, rows: list, visit: dict | None, sites: list
) -> list:
    """Read each drawn row the way an event in the YAML is read (see ``read_event``)."""
    events = []
    for table_name, columns in rows:
        table = known_table(table_name)  # Every module's tables exist.
        entry = dict(columns)
        if visit is not None and table.links_to_visit:
            entry["visit"] = visit
        event = read_event(table, entry, scenario.row_names)
        event.sites = list(sites)
        events.append(event)
    return events
