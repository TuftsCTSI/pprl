"""Test synth's building blocks: random draws, typos and concept hierarchies.

The reliability tests compare the datasets synth writes with stored copies.
These tests check guarantees of the building blocks that a stored copy
cannot show.
"""

import random
from collections import Counter

import jellyfish
import pytest

from pprl.synth.omop_tables import levels_of_separation
from pprl.synth.scenario import (
    Individual,
    PiiVariation,
    Scenario,
    TextVariation,
    Typos,
)
from pprl.synth.seeded_random import (
    SCRAMBLED_ID_LIMIT,
    first_of_rates,
    scrambled_id,
    seeded_rng,
    weighted_choice,
)
from pprl.synth.site_records import add_typos, relation, true_record, vary_text

pytestmark = pytest.mark.reliability

WORDS = ("Last-Name", "O'Example", "Zoë Surname", "12 Elm St", "MA", "x")


class TestSynth:
    """Synthetic data generated from a scenario."""

    def test_streams_are_independent(self) -> None:
        """A random stream depends only on its seed and labels."""
        first = [seeded_rng(7, "person", 1).random() for _ in range(3)]
        assert len(set(first)) == 1, "The same seed and labels give the same draws"
        others = {
            seeded_rng(8, "person", 1).random(),
            seeded_rng(7, "person", 2).random(),
            seeded_rng(7, "site", 1).random(),
        }
        assert first[0] not in others, "Another seed or label gives other draws"

    def test_typos(self) -> None:
        """Each typo is one edit, at a letter or digit, keeping its kind."""
        rng = random.Random(2026)
        for word in WORDS:
            for edits in (1, 2, 3):
                for _ in range(200):
                    typo = add_typos(rng, word, edits)
                    distance = jellyfish.damerau_levenshtein_distance(word, typo)
                    assert distance <= edits, (
                        f"{edits} typo(s) turned {word!r} into {typo!r}"
                    )
                    kept = [c for c in word if not c.isalnum()]
                    assert [c for c in typo if not c.isalnum()] == kept, (
                        f"A typo changed punctuation or spaces: {word!r} -> {typo!r}"
                    )
                    for kind in (str.isdigit, str.isupper, str.islower):
                        before = sum(map(kind, word))
                        after = sum(map(kind, typo))
                        assert before - edits <= after <= before, (
                            f"A typo changed a character's kind: {word!r} -> {typo!r}"
                        )
        assert add_typos(rng, "- -", 2) == "- -", "Nothing to change, no change"

    def test_text_variation(self) -> None:
        """Each kind of variation happens exactly as often as configured."""
        rng = random.Random(2026)
        for word in WORDS:
            assert vary_text(rng, word, TextVariation()) == word, (
                "No variation leaves the text as it is"
            )
            assert vary_text(rng, word, TextVariation(missing=1.0)) is None
            assert vary_text(rng, word, TextVariation(case_change=1.0)) in (
                word.upper(),
                word.lower(),
                word.title(),
            )
            typo = vary_text(rng, word, TextVariation(typos=Typos(rate=1.0)))
            assert typo is not None
            assert jellyfish.damerau_levenshtein_distance(word, typo) <= 1
        assert vary_text(rng, "Zoë", TextVariation(transliteration=1.0)) == "Zoe"

    def test_rates(self) -> None:
        """Choices are drawn with the configured chances."""
        rng = random.Random(2026)
        n = 20_000
        rates = [0.5, 0.3, 0.2]
        counts = Counter(first_of_rates(rng, rates) for _ in range(n))
        for position, rate in enumerate(rates):
            assert abs(counts[position] / n - rate) < 0.02, (
                f"Rate {rate} was drawn {counts[position] / n:.3f} of the time"
            )

        weights = [1, 2, 7]
        values = ["a", "b", "c"]
        counts = Counter(weighted_choice(rng, values, weights) for _ in range(n))
        for value, weight in zip(values, weights, strict=True):
            assert abs(counts[value] / n - weight / sum(weights)) < 0.02
        excluded = {
            weighted_choice(rng, values, weights, exclude="c") for _ in range(500)
        }
        assert excluded == {"a", "b"}, "An excluded value is never drawn"

    def test_scrambled_ids(self) -> None:
        """Scrambled ids never repeat, and stay in range."""
        ids = [scrambled_id("seed", "site_a", n) for n in range(1, 20_001)]
        assert len(set(ids)) == len(ids), "Two people share a scrambled id"
        assert all(1 <= i <= SCRAMBLED_ID_LIMIT for i in ids)
        assert ids[:100] != [scrambled_id("seed", "site_b", n) for n in range(1, 101)]
        for number in (0, SCRAMBLED_ID_LIMIT + 1):
            with pytest.raises(ValueError, match="cannot scramble"):
                scrambled_id("seed", "site_a", number)

    def test_concept_ancestors(self) -> None:
        """Ancestor levels give the shortest and longest path, even with cycles."""
        diamond = {1: [2, 3], 2: [4], 3: [5], 5: [4]}
        assert levels_of_separation(diamond, 1) == {
            2: (1, 1),
            3: (1, 1),
            4: (2, 3),
            5: (2, 2),
        }
        assert levels_of_separation({1: [2], 2: [1]}, 1) == {2: (1, 1)}, (
            "A cycle back to the ancestor is not followed"
        )
        assert levels_of_separation({1: []}, 1) == {}

    def test_relatives(self) -> None:
        """A relative shares the fields of their kind with an earlier individual.

        A scenario without relatives has none.
        """
        individuals = [Individual(i, f"P{i}", ["site_a"]) for i in range(1, 401)]

        def scenario(**relatives: float) -> Scenario:
            return Scenario(
                name="relatives",
                seed=2026,
                sites=["site_a"],
                outputs=["patients"],
                individuals=individuals,
                row_names={},
                shared_rows={},
                attribute_pools=[],
                pii_variation=PiiVariation(),
                relatives=relatives,
                generated=individuals,
            )

        def truth(world: Scenario, individual: Individual) -> dict:
            record = true_record(world, individual, with_pii=True)
            return {**record.pii, "dob": record.birth_date}

        plain = scenario()
        assert all(relation(plain, i) == (None, ()) for i in individuals)
        assert relation(scenario(twin=1), individuals[0]) == (None, ()), (
            "Nobody earlier"
        )
        for kind, same, different in (
            ("twin", ("last_name", "zip", "dob"), ("first_name",)),
            ("junior", ("first_name", "last_name", "zip"), ("dob",)),
            ("family", ("city", "state", "zip"), ("first_name", "dob")),
        ):
            world = scenario(**{kind: 1})
            relative = individuals[-1]
            host, _ = relation(world, relative)
            mine, theirs = truth(world, relative), truth(world, host)
            assert all(mine[key] == theirs[key] for key in same), kind
            assert any(mine[key] != theirs[key] for key in different), kind
        rates = scenario(family=0.25, twin=0)
        share = sum(relation(rates, i)[0] is not None for i in individuals) / 400
        assert 0.15 < share < 0.35, share
        surnames = [
            "last_name" in relation(scenario(family=1), i)[1] for i in individuals[1:]
        ]
        assert 0.6 < sum(surnames) / len(surnames) < 0.8
