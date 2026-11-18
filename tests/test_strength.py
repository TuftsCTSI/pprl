"""Test how strong a linkage is: weak pairs, near misses and warnings.

These are reported around the threshold, within the project's review band.
Only `threshold` decides which records link. The review band only reports.
"""

import shutil
import uuid
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from bitarray import bitarray
from clkhash.serialization import deserialize_bitarray

from pprl import _hash, _link, _pprl_file, _quality, _ui, _wording
from pprl import _messages as msg
from pprl._bundle import bundle_name
from pprl._io import read_text
from pprl._link import _comparing, best_scores
from pprl._pprl_file import file_name
from pprl._review import HISTOGRAM
from pprl._shared import outbox
from tests.helpers import (
    HASH_SIZES,
    SITES,
    SYNTH_FIELDS,
    SYNTH_SECRET,
    THRESHOLD,
    call_kwargs,
    hash_both_sites,
    make_workspace,
    provenance,
    run_in,
    shows,
    synthesize_sites,
    two_site_scenario,
)
from tests.test_merge import ONE_PERSON, MergeCase, by_person, ranked
from tests.test_usability import style_problems

if TYPE_CHECKING:
    from pathlib import Path

    from pprl._pprl_file import PprlFile
    from pprl._wording import Message

pytestmark = pytest.mark.quality


def _quality_found(threshold: float = THRESHOLD) -> dict:
    """Make a linkage's ``quality``, with an estimate and a warning."""
    pair = {
        "sites": list(SITES),
        "linked": 120,
        "linked_pairs": 100,
        "weak": 60,
        "bit_correlation": 0.998,
        "records": {"site_a": 1000, "site_b": 2000},
        "candidates": {"site_a": 107, "site_b": 107},
        "ambiguous": {"site_a": 1, "site_b": 0},
        "false": {"estimate": 7, "low": 2, "high": None},
        "near": {"site_a": {"sample": 57}, "site_b": {"sample": 57}},
    }
    return {
        "threshold": threshold,
        "review_band": list(_quality.band(threshold)),
        "secret_check": True,
        "pairs": [pair],
        "best": [0] * 82 + [8] * 5 + [0] * 8 + [15] * 5,
    }


def _pairs(*rows: tuple) -> tuple:
    """Make candidate pairs in the form anonlink returns them.

    Each row is (score, dataset, record, dataset, record). The result is
    (scores, (datasets, datasets), (records, records)).
    """
    scores, d0, r0, d1, r1 = zip(*rows, strict=True) if rows else ((), (), (), (), ())
    return list(scores), (list(d0), list(d1)), (list(r0), list(r1))


class TestStrength:
    """A linkage's strength, as link finds it."""

    def test_group_strengths(self) -> None:
        """A group's strength is the score of its weakest pair.

        That is the lowest score among the candidate pairs between its
        records. A pair between two groups, or with a record in no group,
        holds no group together.
        """
        pairs = _pairs(
            (0.99, 0, 0, 1, 0),
            (0.97, 0, 1, 1, 2),  # site_a's record 1 and site_b's 2: not grouped
            (0.96, 0, 1, 1, 1),
            (0.95, 0, 2, 1, 0),  # A pair between two groups
        )
        groups = [((0, 0), (1, 0)), ((0, 1), (1, 1)), ((0, 2), (1, 3))]
        strengths, (within, _, _) = _quality.group_strengths(pairs, groups, [3, 4])
        assert strengths.tolist()[:2] == [0.99, 0.96]
        assert sorted(within.tolist()) == [0.96, 0.99]

    def test_summary(self) -> None:
        """Weak pairs, near misses and multiple candidates are counted.

        Weak pairs are counted exactly, and near misses are estimated from the
        sample. Records with several candidates at one site are counted too.
        """
        pairs = _pairs(
            (1.0, 0, 0, 1, 0),
            (0.96, 0, 1, 1, 1),  # A weak pair: below the band's top, 1.0
            (0.955, 0, 1, 1, 2),  # site_a's record 1 has two candidates
        )
        groups = [((0, 0), (1, 0)), ((0, 1), (1, 1))]
        linked = [{0, 1}, {0, 1}]
        best = {  # A sample of 4 of site_a's 8 records, and all 3 of site_b's
            (0, 1): (
                np.array([0, 1, 5, 6]),
                np.array([0.99, 0.96, 0.92, 0.80]),
                np.array([True, True, False, False]),
            ),
            (1, 0): (
                np.array([0, 1, 2]),
                np.array([0.99, 0.96, 0.955]),
                np.array([True, True, False]),
            ),
        }
        quality = _quality.summary(
            ["site_a", "site_b"],
            [8, 3],
            0.95,
            pairs,
            groups,
            linked=linked,
            best=best,
            correlations={(0, 1): 0.9},
        )
        [pair] = quality["pairs"]
        assert (pair["linked"], pair["linked_pairs"], pair["weak"]) == (2, 2, 1)
        assert pair["candidates"] == {"site_a": 2, "site_b": 3}
        assert pair["ambiguous"] == {"site_a": 1, "site_b": 0}
        assert pair["false"]["estimate"] == 0, "under one, among so few records"
        near_a, near_b = pair["near"]["site_a"], pair["near"]["site_b"]
        assert (near_a["in_sample"], near_a["estimate"]) == (1, 2), "1 of 4, of 8"
        assert (near_b["in_sample"], near_b["estimate"]) == (0, 0), "0.955: linkable"
        assert pair["bit_correlation"] == 0.9
        assert pair["records"] == {"site_a": 8, "site_b": 3}
        assert sum(quality["best"]) == 7, "one histogram: every site's sample"
        assert (quality["best"][99], quality["best"][96]) == (2, 2)

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"linked": 97}, msg.LINK_MOST),
            ({"false": {"estimate": 11}}, msg.LINK_MANY_FALSE),
            ({"false": {"estimate": None}}, msg.LINK_MOSTLY_FALSE),
            ({"weak": 60}, msg.LINK_MOSTLY_WEAK),
        ],
    )
    def test_warnings(self, change: dict, message: str) -> None:
        """Link warns about a linkage that is likely bad.

        It warns about a pair that links nearly all of the smaller site, many
        false pairs, or mostly weak pairs.
        """
        pair = {
            "sites": ["site_a", "site_b"],
            "linked": 50,
            "linked_pairs": 100,
            "weak": 10,
            "false": {"estimate": 10},
            "records": {"site_a": 100, "site_b": 200},
        }
        assert _quality.warned({"pairs": [pair]}) == []
        [warning] = _quality.warned({"pairs": [pair | change]})
        assert shows(message, warning), warning
        assert _wording.ESCALATE in warning


class TestBestScores:
    """Each sampled record's best score at another site."""

    def test_against_every_pair(self) -> None:
        """Each sampled hash's best score is its highest Dice similarity.

        The highest is taken over all of the other site's hashes, whether
        compared in chunks or not.
        """
        rng = np.random.default_rng(1)

        def hashes(n: int) -> list[bitarray]:
            found = []
            for _ in range(n):
                bits = bitarray()
                bits.frombytes(rng.integers(0, 256, 16, dtype=np.uint8).tobytes())
                found.append(bits)
            return found

        site, others = hashes(40), hashes(50)
        records = np.sort(rng.choice(40, 30, replace=False))

        def dice(a: bitarray, b: bitarray) -> float:
            return 2 * (a & b).count() / (a.count() + b.count())

        expected = [max(dice(site[r], b) for b in others) for r in records]
        # One chunk; chunks of 13 hashes; chunks of 7 in worker processes
        for workers, chunk in ((1, 10**9), (1, 100), (2, 100)):
            with _comparing([site, others], workers, min_comparisons=0) as compare:
                found = best_scores(
                    compare, 0, records, 1, 50, workers=workers, chunk_comparisons=chunk
                )
            assert np.allclose(found, expected), f"{workers} worker(s), {chunk}"


REPORT = file_name("linkage_report")


def _linked(tmp_path: Path, *, report: bool, shared: int = 490) -> PprlFile:
    """Link two synthetic sites' hashes, and return the linkage file.

    Each site has ``shared`` patients in common, and 10 of its own. With
    ``report``, link also writes its linkage report (see ``_reported``).
    """
    synthesize_sites(tmp_path, two_site_scenario(shared=shared, only_a=10, only_b=10))
    assert hash_both_sites(tmp_path) == [0, 0]
    rc = _link.link(
        hashes=[file_name("hashes", s) for s in SITES],
        threshold=0.95,
        output=file_name("linkages"),
        sites=list(SITES),
        report=REPORT if report else None,
        provenance=provenance(),
        **call_kwargs(tmp_path),
    )
    assert rc == 0
    return _pprl_file.read(tmp_path / file_name("linkages"))


def _reported(folder: Path) -> dict:
    """Return the ``quality`` of the linkage report in ``folder``."""
    report = _pprl_file.read(folder / REPORT, "linkage_report")
    return report.members["quality"]


def _save_report(folder: Path, quality: dict | None = None) -> None:
    """Save a linkage report in the coordinator's workspace ``folder``.

    It is as the linkage agent sends it, for this workspace's project.
    """
    _pprl_file.write(
        folder / REPORT,
        "linkage_report",
        {"quality": quality or _quality_found()},
        provenance=provenance(),
    )


class TestLinkageFile:
    """The strength the linkage file keeps, and the quality the report keeps."""

    def test_strength_and_quality(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The linkage file gives each group's strength; the report, what link found.

        What link found around the threshold is what it reports. The linkage
        file holds no quality, and the report no record.
        """
        linkage = _linked(tmp_path, report=True)
        rows = linkage.members["linkages"]
        assert list(rows.columns) == ["group_id", "site", "study_id", "similarity"]
        assert (rows["similarity"].astype(float) >= 0.95).all()
        assert list(linkage.members) == ["linkages"]
        report = _pprl_file.read(tmp_path / REPORT, "linkage_report")
        assert list(report.members) == ["quality"], "Counts only"
        assert report.header["project"] == linkage.header["project"]
        assert report.header["set"] == linkage.header["set"]
        quality = _reported(tmp_path)
        assert quality["review_band"] == [0.9, 1.0]
        [pair] = quality["pairs"]
        assert pair["linked"] == rows["group_id"].nunique()
        assert pair["near"]["site_a"]["sample"] == 500, "every record of a small site"
        output = capsys.readouterr().out
        assert "just above the threshold" in output

    def test_without_report(self, tmp_path: Path) -> None:
        """Without a report (as in a sweep), link writes no linkage report.

        The linkage file still gives each group's strength.
        """
        linkage = _linked(tmp_path, report=False)
        assert not (tmp_path / REPORT).exists()
        assert "similarity" in linkage.members["linkages"].columns

    def test_merge(self, tmp_path: Path) -> None:
        """Merge never writes a group's strength."""
        case = MergeCase(tmp_path).link(*ONE_PERSON)
        rows = case.linkage_rows
        _pprl_file.write(
            tmp_path / file_name("linkages"),
            "linkages",
            {"linkages": rows.assign(similarity="0.991")},
            provenance=provenance(),
        )
        for site, study_id in (("site_a", "p1"), ("site_b", "p2")):
            case.table("scores", site, study_id=study_id, score="5")
        rules = {"score": ranked("take_highest", column="score")}
        with _ui.run_log(tmp_path / "merge.log", "merge agent", "pprl merge"):
            rc = case.run({"scores": by_person(rules)})
        assert rc == 0
        assert "similarity" not in read_text(tmp_path / "output" / "scores.csv")


class TestFalsePairs:
    """False-pair estimates, checked against counts worked out by hand.

    Each of two sites has 100,000 records, with 400 false pairs and 4,100
    true ones. So a share 400 / 95,900 of records finds a false candidate.
    Of the records with a true counterpart, that is 4,100 * 400 / 95,900 =
    17 at each site.
    """

    CANDIDATES, WITHOUT = [4_500, 4_500], [95_500, 95_500]

    def test_estimate(self) -> None:
        """The count of records with several candidates gives the false pairs.

        A count of zero means no false pairs.
        """
        found = _quality.false_pairs(self.CANDIDATES, self.WITHOUT, 2 * 17)
        assert found is not None
        assert abs(found - 400) <= 3, found
        assert _quality.false_pairs(self.CANDIDATES, self.WITHOUT, 0) == 0

    def test_too_many(self) -> None:
        """Too many records with several candidates for any share gives no answer.

        This shows that many pairs are false.
        """
        assert _quality.false_pairs(self.CANDIDATES, self.WITHOUT, 2_000) is None

    def test_sites_of_different_sizes(self) -> None:
        """A site's records find fewer false candidates at a small site.

        400 false pairs between sites of 1,000,000 and 100,000 records, as each
        site sees them, give the same answer as they would separately.
        """
        linked = [5_400, 5_400]
        without = [994_600, 94_600]
        several = 5_000 * 400 / 995_000 + 5_000 * 400 / 95_000
        found = _quality.false_pairs(linked, without, several)
        assert found is not None
        assert abs(found - 400) <= 2, found

    def test_interval(self) -> None:
        """The interval contains the estimate, and may have no upper limit."""
        found = _quality.false_estimate(self.CANDIDATES, self.WITHOUT, 34)
        assert found["low"] < found["estimate"] < found["high"]
        none = _quality.false_estimate(self.CANDIDATES, self.WITHOUT, 0)
        assert none["estimate"] == none["low"] == 0 < none["high"]
        wide = _quality.false_estimate([500, 500], [99_000, 99_000], 2)
        assert wide["low"] is not None
        assert wide["high"] is None


@pytest.fixture(scope="module")
def cleaned(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Make two sites' cleaned patient files, with few patients in common."""
    folder = tmp_path_factory.mktemp("same_secret")
    synthesize_sites(folder, two_site_scenario(shared=40, only_a=960, only_b=960))
    return folder


class TestSameSecret:
    """The check that sites hashed with one secret."""

    def _correlation(
        self, folder: Path, other_site_hash: dict, xor_folds: int = 0
    ) -> float:
        """Correlate the bits of site_a's hashes with site_b's.

        site_a's are hashed with SYNTH_SECRET and SYNTH_FIELDS. site_b's are
        hashed as ``other_site_hash`` says.
        """
        for site, settings in (("site_a", {}), ("site_b", other_site_hash)):
            rc = _hash.hash_records(
                patients=file_name("clean", site),
                secret=settings.get("secret", SYNTH_SECRET),
                fields=settings.get("fields", SYNTH_FIELDS),
                xor_folds=xor_folds,
                output=file_name("hashes", site),
                **HASH_SIZES,
                provenance=provenance(site),
                **call_kwargs(folder),
            )
            assert rc == 0
        clks = [
            [
                deserialize_bitarray(clk)
                for clk in _pprl_file.read(folder / file_name("hashes", site)).members[
                    "hashes"
                ]["hash"]
            ]
            for site in SITES
        ]
        for site in SITES:
            (folder / file_name("hashes", site)).unlink()
        return _quality.bit_correlations(clks)[(0, 1)]

    def test_same_secret(self, cleaned: Path) -> None:
        """Sites with the same secret agree on how often each bit is set.

        This holds even though they share few patients.
        """
        correlation = self._correlation(cleaned, {})
        assert correlation > 0.9, correlation
        assert _quality.same_secret(correlation), correlation

    def test_different_secret(self, cleaned: Path) -> None:
        """Sites with different secrets do not agree."""
        correlation = self._correlation(cleaned, {"secret": "fedcba9876543210" * 4})
        assert abs(correlation) < 0.15
        assert not _quality.same_secret(correlation)

    def test_different_weights(self, cleaned: Path) -> None:
        """Different weights lower the correlation, but it still shows a shared secret.

        The check is about the secret only. A bundle set's header is what keeps
        the weights the same.
        """
        fields = {
            name: {**spec, "weight": 100} if isinstance(spec, dict) else 100
            for name, spec in SYNTH_FIELDS.items()
        }
        correlation = self._correlation(cleaned, {"fields": fields})
        assert _quality.same_secret(correlation), correlation
        assert correlation < self._correlation(cleaned, {}), correlation

    @pytest.mark.parametrize("xor_folds", [1, 2, 3])
    def test_folded(self, cleaned: Path, xor_folds: int) -> None:
        """With folds and new weights, the check still tells one secret from two."""
        fields = {
            name: {**spec, "weight": 100} if isinstance(spec, dict) else 100
            for name, spec in SYNTH_FIELDS.items()
        }
        for other_site_hash, same in (
            ({}, True),
            ({"fields": fields}, True),
            ({"secret": "fedcba9876543210" * 4}, False),
        ):
            correlation = self._correlation(
                cleaned, other_site_hash, xor_folds=xor_folds
            )
            assert _quality.same_secret(correlation) == same, correlation

    def test_too_few_records(self) -> None:
        """Nothing is checked if a site has too few records or bits that never vary.

        No warning is given.
        """
        rng = np.random.default_rng(5)

        def hashes(n: int) -> list[bitarray]:
            found = []
            for _ in range(n):
                bits = bitarray()
                bits.frombytes(rng.integers(0, 256, 128, dtype=np.uint8).tobytes())
                found.append(bits)
            return found

        few = hashes(_quality.SAME_SECRET_RECORDS - 1)
        enough = _quality.SAME_SECRET_RECORDS
        empty = [bitarray("0" * 1024)] * enough
        found = _quality.bit_correlations([few, hashes(enough), empty])
        assert found[(0, 1)] is None
        assert found[(0, 2)] is None
        assert found[(1, 2)] is None, "hashes with no bit set: no share varies"
        assert _quality.same_secret(None)

    def test_link_stops(
        self, cleaned: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Link stops before comparing a pair of sites with different secrets.

        It says which pair, and writes nothing.
        """
        for site, secret in zip(
            SITES, (SYNTH_SECRET, "fedcba9876543210" * 4), strict=True
        ):
            assert (
                _hash.hash_records(
                    patients=file_name("clean", site),
                    secret=secret,
                    fields=SYNTH_FIELDS,
                    output=file_name("hashes", site),
                    **HASH_SIZES,
                    provenance=provenance(site),
                    **call_kwargs(cleaned),
                )
                == 0
            )
        capsys.readouterr()
        rc = _link.link(
            hashes=[file_name("hashes", s) for s in SITES],
            threshold=0.95,
            output=file_name("linkages"),
            sites=list(SITES),
            report=REPORT,
            provenance=provenance(),
            **call_kwargs(cleaned),
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert shows(msg.LINK_OTHER_SECRET, captured.err), captured.err
        assert shows(msg.NOTHING_WRITTEN, captured.err), captured.err
        assert not shows(msg.NOTHING_LINKED_PAIR, captured.out), "Nothing was compared"
        assert not (cleaned / file_name("linkages")).exists()
        assert not (cleaned / REPORT).exists()
        for site in SITES:
            (cleaned / file_name("hashes", site)).unlink()

    def test_turned_off(
        self, cleaned: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A bundle can turn the check off, for sites that salt each record.

        With `record_salt`, the check fails even with one secret, so link
        stops. Turned off, link compares the sites, keeps no correlation, and
        pprl review says the check did not run.
        """
        for site in SITES:
            assert (
                _hash.hash_records(
                    patients=file_name("clean", site),
                    secret=SYNTH_SECRET,
                    fields=SYNTH_FIELDS,
                    record_salt={"field": "zip", "length": 3},
                    output=file_name("hashes", site),
                    **HASH_SIZES,
                    provenance=provenance(site),
                    **call_kwargs(cleaned),
                )
                == 0
            )
        capsys.readouterr()
        for secret_check in (True, False):
            rc = _link.link(
                hashes=[file_name("hashes", s) for s in SITES],
                threshold=0.95,
                output=file_name("linkages"),
                sites=list(SITES),
                report=REPORT,
                secret_check=secret_check,
                provenance=provenance(),
                **call_kwargs(cleaned),
            )
            captured = capsys.readouterr()
            assert rc == (1 if secret_check else 0), captured.err
            assert shows(msg.LINK_OTHER_SECRET, captured.err) == secret_check
        quality = _reported(cleaned)
        assert quality["secret_check"] is False
        assert quality["pairs"][0]["bit_correlation"] is None
        for name in (
            file_name("linkages"),
            REPORT,
            *(file_name("hashes", s) for s in SITES),
        ):
            (cleaned / name).unlink()
        make_workspace(tmp_path, role="coordinator")
        _save_report(tmp_path, quality)
        rc, output = run_in(tmp_path, ["review"])
        assert rc == 0, output
        assert shows(msg.REVIEW_SECRET_OFF, output), output
        assert not shows(msg.REVIEW_SAME_SECRET, output), output


def _review_refused(folder: Path, says: Message | str) -> str:
    """Run pprl review in ``folder``, and check that it is refused.

    The output shows ``says`` and follows the rules for messages. Return the
    output.
    """
    rc, output = run_in(folder, ["review"])
    assert rc == 1, output
    assert shows(says, output), output
    assert not style_problems(output, rejected=True), output
    return output


class TestReview:
    """Pprl review's checklist and histogram, from the linkage report."""

    def test_checklist(self, tmp_path: Path) -> None:
        """Pprl review shows the checklist and draws the histogram.

        Each checklist item comes from the linkage report. The single
        histogram uses a log scale, and is written as an SVG beside the report.
        """
        make_workspace(tmp_path, role="coordinator")
        _save_report(tmp_path)
        rc, output = run_in(tmp_path, ["review"])
        assert rc == 0, output
        for message in (
            msg.REVIEW_SAME_SECRET,
            msg.REVIEW_PAIR,
            msg.REVIEW_BEST,
            msg.REVIEW_FALSE_OPEN,
            msg.REVIEW_WARNING,
            msg.REVIEW_WROTE,
        ):
            assert shows(message, output), (message, output)
        for text in (
            "bit correlation 1.00",
            "site_a-site_b 120 (site_a 12.0%, site_b 6.0%)",
            "Best scores at each other site, 115",
            "site_a-site_b 7 (95% range 2 or more)",
            "scored just above the threshold",
        ):
            assert text in " ".join(output.split()), (text, output)
        svg = ET.parse(tmp_path / HISTOGRAM).getroot()  # noqa: S314 (pprl wrote it)
        assert svg.tag == "{http://www.w3.org/2000/svg}svg", svg.tag
        drawn = read_text(tmp_path / HISTOGRAM)
        assert msg.REVIEW_RECORDS(sample=_quality.SAMPLE) in drawn
        assert "10,000" not in drawn

    def test_inspect(self, tmp_path: Path) -> None:
        """Pprl inspect shows what a linkage report holds, as review does."""
        make_workspace(tmp_path, role="coordinator")
        _save_report(tmp_path)
        rc, output = run_in(tmp_path, ["inspect", REPORT])
        assert rc == 0, output
        assert msg.FILE_KINDS["linkage_report"] in output, output
        for message in (msg.REVIEW_SAME_SECRET, msg.REVIEW_PAIR, msg.REVIEW_FALSE_OPEN):
            assert shows(message, output), (message, output)
        assert not (tmp_path / HISTOGRAM).exists(), "Inspect writes nothing"

    def test_unchecked_and_mostly_false(self, tmp_path: Path) -> None:
        """Review plainly says what it could not find.

        A check that could not run, an estimate with no answer, and the lack of
        warnings are each stated.
        """
        make_workspace(tmp_path, role="coordinator")
        quality = _quality_found()
        pair = quality["pairs"][0]
        pair |= {"weak": 1}
        pair["false"] = {"estimate": 0, "low": 0, "high": 3}
        unchecked = {
            **pair,
            "sites": ["site_a", "site_c"],
            "linked": 5,
            "records": {"site_a": 1000, "site_c": 10},
            "bit_correlation": None,
            "candidates": {"site_a": 5, "site_c": 5},
            "ambiguous": {},
            "false": {"estimate": None, "low": None, "high": None},
            "near": {},
        }
        quality["pairs"].append(unchecked)
        _save_report(tmp_path, quality)
        rc, output = run_in(tmp_path, ["review"])
        assert rc == 0, output
        assert shows(msg.REVIEW_SECRET_UNCHECKED, output), output
        assert shows(msg.REVIEW_MOSTLY_FALSE, output), output

    def test_nothing_to_review(self, tmp_path: Path) -> None:
        """Pprl review says when the report is missing, or not this project's.

        It never overwrites an existing histogram.
        """
        make_workspace(tmp_path, role="coordinator")
        _review_refused(tmp_path, msg.REVIEW_NOT_FOUND)
        _pprl_file.write(
            tmp_path / REPORT,
            "linkage_report",
            {"quality": _quality_found()},
            provenance={**provenance(), "project": str(uuid.uuid4())},
        )
        _review_refused(tmp_path, msg.FILE_OTHER_PROJECT)
        assert not (tmp_path / HISTOGRAM).exists(), "A refused run draws nothing"
        (tmp_path / REPORT).unlink()
        _save_report(tmp_path)
        assert run_in(tmp_path, ["review"])[0] == 0
        drawn = (tmp_path / HISTOGRAM).read_bytes()
        rc, output = run_in(tmp_path, ["review"])
        assert rc == 1, "Never replaced"
        assert not style_problems(output, rejected=True), output
        assert (tmp_path / HISTOGRAM).read_bytes() == drawn

    def test_other_set(self, tmp_path: Path) -> None:
        """A report from another set of bundles than the linkage agent's is refused."""
        make_workspace(tmp_path, role="coordinator")
        bundle = tmp_path / outbox("linkage", bundle_name("linkage"))
        bundle.parent.mkdir(parents=True)
        _pprl_file.write(
            bundle,
            "bundle",
            {"settings": {"name": "Test project"}},
            provenance={**provenance(), "set": str(uuid.uuid4())},
            role="linkage",
        )
        _save_report(tmp_path)
        _review_refused(tmp_path, msg.FILE_OTHER_SET)
        assert not (tmp_path / HISTOGRAM).exists(), "A refused run draws nothing"

    def test_end_to_end(self, tmp_path: Path) -> None:
        """What link reports is what pprl review reads, from a real run.

        The sites are just large enough for the same-secret check.
        """
        records = _quality.SAME_SECRET_RECORDS
        _linked(tmp_path, report=True, shared=records - 10)
        quality = _reported(tmp_path)
        assert sum(quality["best"]) == 2 * records, "every record of both sites"
        correlation = quality["pairs"][0]["bit_correlation"]
        assert correlation > _quality.SAME_SECRET
        coordinator = tmp_path / "coordinator"
        make_workspace(coordinator, role="coordinator")
        shutil.copy(tmp_path / REPORT, coordinator / REPORT)
        rc, output = run_in(coordinator, ["review"])
        assert rc == 0, output
        assert shows(msg.REVIEW_SAME_SECRET, output), output
