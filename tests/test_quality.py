"""Test linkage quality.

These tests measure linkage quality against known ground truth in two ways:

- ABSOLUTE: Precision and recall meet minimum levels under realistic data
  degradation (typos, transpositions).

- MONOTONIC: Stricter thresholds increase precision and decrease recall.
  This confirms that the system behaves predictably as the operator tunes
  sensitivity.

Patient files come from synth scenarios, where both sites vary their copy
of each individual independently. The ground truth comes from the scenario:
records at different sites with the same study_id are the same person. (The
study_ids are synth's person_ids.)
"""

import shutil
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from clkhash.schema import from_json_dict

from pprl import _link
from pprl._hash import _hash_rows
from pprl._metrics import count_true_pairs, precision_recall
from pprl._schema import PREPARERS, expand_schema
from tests.helpers import (
    HASH_SIZES,
    SITES,
    SYNTH_SECRET,
    call_kwargs,
    hash_both_sites,
    members,
    provenance,
    synthesize_sites,
    two_site_scenario,
)

if TYPE_CHECKING:
    from pathlib import Path

    from bitarray import bitarray

pytestmark = pytest.mark.quality

HASH_FILES = ["site_a.hashes.pprl", "site_b.hashes.pprl"]


def _typos(rate: float) -> dict:
    """Give each name and address field at each site one typo with this chance."""
    typos = {"typos": {"rate": rate, "max_edits": 1}}
    return {"name": typos, "address": typos}


class TestQuality:
    """Linkage quality."""

    n_patients_per_dataset = 1000
    n_expected_matches = 100

    # =========================================================================
    # Absolute precision and recall
    # =========================================================================

    @pytest.mark.extended
    def test_realistic_data(self, tmp_path: Path) -> None:
        """High recall and 0 false positives when ~10% of fields differ."""
        metrics = self._measure_linkage_accuracy(
            tmp_path, seed="realistic", typo_rate=0.05, threshold=0.95
        )
        assert metrics["fp"] == 0, (
            f"{metrics['fp']} false positives (precision={metrics['precision']:.3f})"
        )
        assert metrics["recall"] >= 0.90, (
            f"Recall {metrics['recall']:.3f} < 0.90 ({metrics['fn']} false negatives)"
        )

    @pytest.mark.extended
    def test_very_poor_data(self, tmp_path: Path) -> None:
        """Decent recall and 0 false positives when ~25% of fields differ."""
        metrics = self._measure_linkage_accuracy(
            tmp_path, seed="poor", typo_rate=0.13, threshold=0.95
        )
        assert metrics["fp"] == 0, (
            f"{metrics['fp']} false positives (precision={metrics['precision']:.3f})"
        )
        assert metrics["recall"] >= 0.50, (
            f"Recall {metrics['recall']:.3f} < 0.50 ({metrics['fn']} false negatives)"
        )

    @pytest.mark.parametrize(
        "other",
        [
            "Example Mary Ann",
            "Ann Example, Mary",
            "MARY-ANN  EXAMPLE",
            "example mary ann",
        ],
    )
    def test_word_order_gives_the_same_hash(self, other: str) -> None:
        """Values that differ only in word order, punctuation or case hash the same.

        The hashes are exactly the same, not just similar.
        """
        prepare = PREPARERS["text"]
        rows = pd.DataFrame(
            {
                "study_id": ["1", "2"],
                "name": [prepare("Mary Ann Example"), prepare(other)],
            }
        )
        schema = from_json_dict(expand_schema({"name": 1}, **HASH_SIZES))
        first, second = _hash_rows(rows, SYNTH_SECRET, schema)
        assert first == second

    @pytest.mark.parametrize("xor_folds", [1, 2, 3])
    def test_folding_folds(self, xor_folds: int) -> None:
        """xor_folds hashes into more bits and folds them, and clkhash really does.

        clkhash hashes into 2 ** xor_folds times the bits, then folds them in
        half that many times. This matters because clkhash silently ignores a
        key with the wrong name.
        """
        rows = pd.DataFrame(
            {
                "study_id": ["1"],
                "name": [PREPARERS["text"]("Mary Ann Example")],
            }
        )

        def hashed(schema: dict) -> bitarray:
            return _hash_rows(rows, SYNTH_SECRET, from_json_dict(schema))[0]

        unfolded = expand_schema({"name": 1}, **HASH_SIZES)
        assert unfolded["clkConfig"]["xor_folds"] == 0
        longer = expand_schema({"name": 1}, **HASH_SIZES)
        longer["clkConfig"]["l"] *= 2**xor_folds
        whole = hashed(longer)
        for _ in range(xor_folds):
            middle = len(whole) // 2
            whole = whole[:middle] ^ whole[middle:]
        folded = hashed(expand_schema({"name": 1}, **HASH_SIZES, xor_folds=xor_folds))
        assert len(folded) == 1024
        assert folded == whole
        assert folded != hashed(unfolded)

    def test_project_salt_changes_every_hash(self) -> None:
        """A project salt changes every hash, but equal values still hash alike."""
        prepare = PREPARERS["text"]
        rows = pd.DataFrame(
            {"study_id": ["1", "2"], "name": [prepare("Mary Surname")] * 2}
        )

        def hashed(salt: str | None) -> list:
            schema = from_json_dict(
                expand_schema({"name": 1}, **HASH_SIZES, project_salt=salt)
            )
            return _hash_rows(rows, SYNTH_SECRET, schema)

        plain, salted, other = hashed(None), hashed("one"), hashed("two")
        assert salted[0] == salted[1]
        assert len({plain[0].to01(), salted[0].to01(), other[0].to01()}) == 3

    @pytest.mark.parametrize("workers", [1, 2])
    def test_record_salt_separates_records(self, workers: int | None) -> None:
        """Records hash alike only if their record salts agree.

        Each record uses its own row's salt, and the salted field is cut to
        its length.
        """
        prepare = PREPARERS["text"]
        fields = {"name": 1, "zip": {"weight": 1, "type": "zip"}}
        rows = pd.DataFrame(
            {
                "study_id": ["1", "2", "3", "4"],
                "name": [prepare("Mary Surname")] * 4,
                "zip": ["02101", "10001", "02101", ""],
            }
        )
        schema = from_json_dict(expand_schema(fields, **HASH_SIZES))
        salt = {"field": "zip", "length": 3}
        salted = _hash_rows(rows, SYNTH_SECRET, schema, workers, 2, record_salt=salt)
        plain = _hash_rows(rows, SYNTH_SECRET, schema)

        one_by_one = [
            _hash_rows(rows.iloc[[i]], SYNTH_SECRET, schema, 1, record_salt=salt)[0]
            for i in range(len(rows))
        ]
        assert salted == one_by_one, "Each hash is in its own row's place"
        assert salted[0] == salted[2], "One value, one salt"
        assert len({salted[i].to01() for i in (0, 1, 3)}) == 3
        assert salted[0] != plain[0], "A record salt changes the hash"

        names_alike = pd.DataFrame(
            {
                "study_id": ["1", "2"],
                "name": ["MARY SURNAME"] * 2,
                "zip": ["02101", "02199"],
            }
        )
        cut = _hash_rows(names_alike, SYNTH_SECRET, schema, 1, record_salt=salt)
        whole = _hash_rows(
            names_alike, SYNTH_SECRET, schema, 1, record_salt={"field": "zip"}
        )
        shared_cut = (cut[0] & cut[1]).count()
        shared_whole = (whole[0] & whole[1]).count()
        assert shared_cut > shared_whole, "Only the first digits salted the first pair"

    def test_record_salt_follows_the_project_salt(self) -> None:
        """The two salts work together: a different project salt gives other hashes."""
        fields = {"name": 1, "zip": {"weight": 1, "type": "zip"}}
        rows = pd.DataFrame(
            {"study_id": ["1"], "name": ["MARY SURNAME"], "zip": ["02101"]}
        )

        def hashed(salt: str | None) -> list:
            schema = from_json_dict(
                expand_schema(fields, **HASH_SIZES, project_salt=salt)
            )
            return _hash_rows(rows, SYNTH_SECRET, schema, record_salt={"field": "zip"})[
                0
            ]

        assert hashed("one") != hashed("two")

    def test_a_different_name_gives_a_different_hash(self) -> None:
        """Sorting the words changes nothing else: a different name still differs."""
        prepare = PREPARERS["text"]
        rows = pd.DataFrame(
            {
                "study_id": ["1", "2"],
                "name": [prepare("Mary Surname"), prepare("Ann Surname")],
            }
        )
        schema = from_json_dict(expand_schema({"name": 1}, **HASH_SIZES))
        first, second = _hash_rows(rows, SYNTH_SECRET, schema)
        assert first != second

    # =========================================================================
    # Monotonic threshold behavior
    # =========================================================================

    def test_threshold_sweep(self, tmp_path: Path) -> None:
        """Precision increases and recall decreases with threshold."""
        synthesize_sites(
            tmp_path,
            two_site_scenario(shared=100, seed="sweep", pii=_typos(0.05)),
        )
        results = hash_both_sites(tmp_path)
        assert all(rc == 0 for rc in results), "hashing failed"
        n_true = self._true_pairs(tmp_path)
        assert n_true == 100, "Both sites should share all 100 individuals"

        thresholds = [0.80, 0.85, 0.90, 0.95, 0.975, 0.99]
        results = []

        for t in thresholds:
            link_dir = tmp_path / f"link_{t:.3f}"
            link_dir.mkdir()
            for name in HASH_FILES:
                shutil.copy(tmp_path / name, link_dir / name)

            rc = _link.link(
                hashes=HASH_FILES,
                threshold=t,
                output="linkages.pprl",
                provenance=provenance(),
                **call_kwargs(link_dir),
            )
            assert rc == 0, f"link at threshold={t} failed"
            linkages = members(link_dir / "linkages.pprl", "linkages")
            pr = precision_recall(linkages, n_true)
            pr["threshold"] = t
            results.append(pr)

        precisions = [r["precision"] for r in results]
        for i in range(len(precisions) - 1):
            assert precisions[i] <= precisions[i + 1] + 0.01, (
                f"Precision should not decrease: "
                f"{precisions[i]:.3f} at t={results[i]['threshold']} > "
                f"{precisions[i + 1]:.3f} at t={results[i + 1]['threshold']}"
            )

        recalls = [r["recall"] for r in results]
        for i in range(len(recalls) - 1):
            assert recalls[i] >= recalls[i + 1] - 0.01, (
                f"Recall should not increase: "
                f"{recalls[i]:.3f} at t={results[i]['threshold']} < "
                f"{recalls[i + 1]:.3f} at t={results[i + 1]['threshold']}"
            )

        assert results[0]["recall"] >= 0.8, (
            f"Expected recall >= 0.8 at threshold=0.80, got {results[0]['recall']:.3f}"
        )

        assert results[-1]["precision"] == 1.0, (
            "Expected precision=1.0 at threshold=0.99, "
            f"got {results[-1]['precision']:.3f}"
        )

    # =========================================================================
    # Helpers
    # =========================================================================

    def _true_pairs(self, tmp_path: Path) -> int:
        return count_true_pairs(
            [members(tmp_path / f"{site}.clean.pprl", "patients") for site in SITES]
        )

    def _measure_linkage_accuracy(
        self, tmp_path: Path, seed: int | str, threshold: float, typo_rate: float
    ) -> dict:
        """Link two sites that share 100 of their 1000 individuals each."""
        n_shared = self.n_expected_matches
        n_unique = self.n_patients_per_dataset - n_shared
        synthesize_sites(
            tmp_path,
            two_site_scenario(
                shared=n_shared,
                only_a=n_unique,
                only_b=n_unique,
                seed=seed,
                pii=_typos(typo_rate),
            ),
        )
        n_true = self._true_pairs(tmp_path)
        assert n_true == n_shared, (
            f"Ground truth violated: expected {n_shared} shared individuals"
        )

        results = hash_both_sites(tmp_path)
        assert all(rc == 0 for rc in results), "hashing failed"

        link_dir = tmp_path / "link_output"
        link_dir.mkdir()
        rc = _link.link(
            hashes=HASH_FILES,
            threshold=threshold,
            output="linkages.pprl",
            provenance=provenance(),
            **call_kwargs(tmp_path, output_folder=link_dir),
        )
        assert rc == 0, f"link failed with exit code {rc}"

        linkages = members(link_dir / "linkages.pprl", "linkages")
        return precision_recall(linkages, n_true)
