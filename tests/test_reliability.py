"""Test reliability.

These tests verify that commands produce consistent, deterministic output,
and that each command's output is a valid input to the next.

The PPRL test cases (exact, capitalization, missing, typos, shuffled,
disjoint) are small synth scenarios, one config file per case. Each case
runs through synth, then hash, then link. Synth output is compared with
stored files (regenerate them with UPDATE_FIXTURES=1). Linkage results are
checked against the scenario's ground truth: matching records share a
person_id. The example scenarios are also compared with stored output, and
generated histories are checked to be split between sites as their
scenario says.
"""

import io
import shutil
import signal
from typing import TYPE_CHECKING, Never

import anonlink
import pandas as pd
import pytest
import yaml
from bitarray import bitarray
from clkhash import clk
from clkhash.serialization import deserialize_bitarray, serialize_bitarray

from pprl import _clean, _hash, _link, _pprl_file, _ui
from pprl import _messages as msg
from pprl._hash import _hash_rows
from pprl._io import (
    read_csv_frame,
    read_text,
    write_csv_frame,
    write_text,
)
from pprl._link import _candidate_pairs, _comparing
from pprl._metrics import count_true_pairs, precision_recall
from pprl._rules import in_order
from pprl._shared import STUDY_ID
from pprl._workers import worker_pool
from pprl.synth import synthesize
from pprl.synth.clinical_modules import MODULE_CONCEPTS, MODULES
from pprl.synth.omop_cdm import OMOP_TABLES
from tests.helpers import (
    CONFIG_DIR,
    EXAMPLES_DIR,
    EXPECTED_DIR,
    HASH_SIZES,
    SITES,
    SYNTH_FIELDS,
    SYNTH_ID,
    SYNTH_SECRET,
    assert_folder_matches,
    assert_output_matches,
    call_kwargs,
    files_in,
    log_of,
    make_workspace,
    members,
    provenance,
    run_in,
    run_scenario,
    shows,
    strip_ansi,
    synthesize_patients,
    two_site_scenario,
    write_bundle,
    write_cleaned,
    write_selected,
    write_yaml,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from pathlib import Path

    from clkhash.schema import Schema

pytestmark = pytest.mark.reliability

# Each test case, and the threshold its linkage runs at.
CASES = {
    "exact": 0.975,
    "caps_full": 0.975,
    "caps_partial": 0.975,
    "missing_light": 0.85,
    "missing_heavy": 0.80,
    "typo_light": 0.90,
    "typo_heavy": 0.80,
    "shuffled": 0.975,
    "disjoint": 0.975,
}
# Each example scenario (in examples/scenarios), and the folder its dataset is
# written to and stored under (in expected/examples).
EXAMPLE_OUTPUTS = {
    "boston": "boston_output",
    "linkage_3_sites.yml": "linkage_3_sites",
    "histories_3_sites.yml": "histories_3_sites",
}
# Cases whose sites record every shared individual identically, apart from
# letter case and row order. Linkage must find exactly the true pairs.
CLEAN_CASES = ("exact", "caps_full", "caps_partial", "shuffled")
# The minimum (precision, recall) that each case with noisy records must reach
# at its threshold. They are set a little below what pprl reached when they
# were chosen (2026-10-27: precision 1.00 for every case; recall 0.92 with
# light missing values, 0.93 with heavy, and 1.00 with typos). This way a
# change that links worse is caught.
CRITERIA = {
    "missing_light": (0.95, 0.90),
    "missing_heavy": (0.95, 0.85),
    "typo_light": (0.95, 0.95),
    "typo_heavy": (0.95, 0.95),
}
PATIENT_COLUMNS = ["person_id", "name", "city", "state", "zip", "dob"]
# Any modules would do. These ones write conditions and measurements.
HISTORY_MODULES = ("hypertension", "blood_pressure", "fobt", "fit_dna")

# Person-level tables that hold events, as opposed to rows synth builds itself.
EVENT_TABLES = [
    table
    for table in OMOP_TABLES
    if table.is_person_level
    and table.name not in ("person", "observation_period", "visit_occurrence")
]


def _run_case(folder: Path, case: str) -> tuple[Path, list[str], Path]:
    """Run a test case through synth, hash and link in ``folder``.

    Each site's patient file is hashed in the form clean writes, but with
    its person_ids as study_ids. This keeps the
    hashes and linkage the same every time, and the person_ids give the
    ground truth.

    Return the dataset folder, the hash file names and the linkage file.
    """
    shutil.copy(CONFIG_DIR / f"synth_{case}.yml", folder)
    rc = synthesize(f"synth_{case}.yml", **call_kwargs(folder))
    assert rc == 0, f"{case}: synth returned {rc}"

    hash_config = yaml.safe_load(read_text(CONFIG_DIR / "hash.yml"))
    hashes = []
    for site in SITES:
        output = f"{case}_{site}.hashes.pprl"
        cleaned = f"{case}_{site}.clean.pprl"
        patients = read_csv_frame(folder / f"synth_{case}/{site}/patients.csv")
        write_cleaned(patients, folder / cleaned, site)
        rc = _hash.hash_records(
            patients=cleaned,
            output=output,
            provenance=provenance(site),
            **hash_config,
            **call_kwargs(folder),
        )
        assert rc == 0, f"{case}: hash {site} returned {rc}"
        hashes.append(output)

    linkages = f"{case}_linkages.pprl"
    rc = _link.link(
        hashes=hashes,
        threshold=CASES[case],
        output=linkages,
        sites=list(SITES),
        provenance=provenance(),
        **call_kwargs(folder),
    )
    assert rc == 0, f"{case}: link returned {rc}"
    return folder / f"synth_{case}", hashes, folder / linkages


@pytest.fixture(scope="module")
def pipeline(
    tmp_path_factory: pytest.TempPathFactory,
) -> Callable[[str], tuple[Path, list[Path], Path]]:
    """Run each test case through synth, hash and link once per test module.

    Return a function that takes a case name and returns the case's dataset
    folder, hash file paths and linkage file.
    """
    folder = tmp_path_factory.mktemp("cases")
    runs = {}

    def run(case: str) -> tuple[Path, list[Path], Path]:
        if case not in runs:
            dataset, hashes, linkages = _run_case(folder, case)
            runs[case] = dataset, [folder / h for h in hashes], linkages
        return runs[case]

    return run


def _history_scenario(
    modules: Sequence[str] = HISTORY_MODULES, **overrides: object
) -> dict:
    """Make two sites whose individuals have generated histories from ``modules``."""
    scenario = {
        "seed": "histories",
        "sites": list(SITES),
        "outputs": ["omop", "patients"],
        "birth_years": [1925, 2020],
        "measurement_years": [2023, 2024],
        "generated_individuals": [
            {
                "sites": list(SITES),
                "count": 40,
                "modules": {module: {} for module in modules},
            }
        ],
    }
    scenario.update(overrides)
    return scenario


def _events(dataset: Path, site: str, individual_of: dict | None = None) -> list:
    """List every event at ``site`` as (person, table, start date, other columns).

    The list is sorted. Ids that synth numbers per site (the row's own id and
    its visit) are left out. ``individual_of`` maps the site's person_ids to
    individual ids.
    """
    events = []
    for table in EVENT_TABLES:
        df = read_csv_frame(dataset / site / "OMOP" / f"{table.name}.csv")
        people = (
            df["person_id"].map(individual_of) if individual_of else df["person_id"]
        )
        ids = {table.primary_key, "person_id", "visit_occurrence_id"}
        others = [column for column in df.columns if column not in ids]
        for person, (_, row) in zip(people, df.iterrows(), strict=True):
            start = row[table.start_date_column]
            events.append((person, table.name, start, tuple(row[others])))
    return sorted(events)


class TestReliability:
    """The pipeline gives the same results every time."""

    @pytest.mark.parametrize("case", CASES)
    def test_synth(
        self, pipeline: Callable[[str], tuple[Path, list[Path], Path]], case: str
    ) -> None:
        """Each test-case scenario produces the same patient files every time."""
        dataset, _, _ = pipeline(case)
        for site in SITES:
            df = read_csv_frame(dataset / site / "patients.csv")
            assert list(df.columns) == PATIENT_COLUMNS, (
                f"{case}: unexpected patient file columns {list(df.columns)}"
            )
            assert len(df) == 100, f"{case}: expected 100 rows at {site}"
        assert_folder_matches(dataset, EXPECTED_DIR / "synth" / case)

    def test_same_hashes(self, tmp_path: Path) -> None:
        """The same records, hashed with the same secret, give the stored hashes.

        They match bit for bit. Hashing must not change with any library's
        release (clkhash, bitarray, cryptography), so every site's hashes
        match whichever release each site installed. The records include
        names with accents and punctuation, and empty fields.
        """
        rows = pd.DataFrame(
            {
                "person_id": [str(i) for i in range(1, 7)],
                "name": [
                    "Jo O'Example",
                    "José Surname",
                    "Sam Last",
                    "",
                    "Anne-Marie Example",
                    "Li Last-Name",
                ],
                "city": ["Boston", "Salem", "Worcester", "Lowell", "", "Quincy"],
                "state": ["MA", "MA", "NH", "MA", "RI", ""],
                "zip": ["02101", "01970", "03031", "", "02903", "02169"],
                "dob": [
                    "1990-03-15",
                    "1985-12-01",
                    "",
                    "2001-07-04",
                    "1947-01-30",
                    "1999-02-28",
                ],
            }
        )
        write_cleaned(rows, tmp_path / "site_a.clean.pprl", "site_a")
        rc = _hash.hash_records(
            patients="site_a.clean.pprl",
            secret=SYNTH_SECRET,
            fields=SYNTH_FIELDS,
            output="site_a.hashes.pprl",
            **HASH_SIZES,
            provenance=provenance("site_a"),
            **call_kwargs(tmp_path),
        )
        assert rc == 0
        hashes = members(tmp_path / "site_a.hashes.pprl", "hashes")
        write_text(
            tmp_path / "hashes.csv", hashes.to_csv(index=False, lineterminator="\n")
        )
        assert_output_matches(tmp_path / "hashes.csv", EXPECTED_DIR / "same_hashes.csv")

    @pytest.mark.parametrize("case", CASES)
    def test_hash(
        self, pipeline: Callable[[str], tuple[Path, list[Path], Path]], case: str
    ) -> None:
        """Each site's patient file hashes into one hash per record."""
        dataset, hashes, _ = pipeline(case)
        for site, name in zip(SITES, hashes, strict=True):
            hash_file = _pprl_file.read(name, "hashes", site=site)
            df = hash_file.members["hashes"]
            assert list(df.columns) == ["study_id", "hash"], (
                f"{case}: unexpected hash columns {list(df.columns)}"
            )
            patients = read_csv_frame(dataset / site / "patients.csv")
            assert list(df["study_id"]) == list(patients["person_id"]), (
                f"{case}: hash rows do not follow the patient file at {site}"
            )

    @pytest.mark.parametrize("case", CASES)
    def test_link(
        self, pipeline: Callable[[str], tuple[Path, list[Path], Path]], case: str
    ) -> None:
        """Linkage finds the true pairs, judged by the scenario's ground truth."""
        _, hashes, linkage_path = pipeline(case)
        linkages = members(linkage_path, "linkages")
        assert list(linkages.columns) == [
            "group_id",
            "site",
            "study_id",
            "similarity",
        ], f"{case}: unexpected linkage columns {list(linkages.columns)}"
        strengths = linkages.groupby("group_id")["similarity"]
        assert (strengths.nunique() <= 1).all(), "One strength for each group"  # noqa: PD101 (per group)
        assert (linkages["similarity"].astype(float) >= CASES[case] - 0.0005).all(), (
            "Each group's weakest pair is at or above the threshold"
        )
        n_true = count_true_pairs([members(h, "hashes") for h in hashes])
        if case == "disjoint":
            assert n_true == 0, "disjoint: sites should share no individual"
            assert len(linkages) == 0, "disjoint: nothing should link"
        else:
            assert n_true == 100, f"{case}: expected 100 true pairs"
            pr = precision_recall(linkages, n_true)
            if case in CLEAN_CASES:
                assert pr["precision"] == 1.0, (
                    f"{case}: expected every true pair and nothing else, "
                    f"got precision={pr['precision']:.3f}, "
                    f"recall={pr['recall']:.3f}"
                )
                assert pr["recall"] == 1.0, (
                    f"{case}: expected every true pair and nothing else, "
                    f"got precision={pr['precision']:.3f}, "
                    f"recall={pr['recall']:.3f}"
                )
            else:
                precision, recall = CRITERIA[case]
                assert pr["precision"] >= precision, (
                    f"{case}: precision {pr['precision']:.3f} < {precision}"
                )
                assert pr["recall"] >= recall, (
                    f"{case}: recall {pr['recall']:.3f} < {recall}"
                )
        for group_id, group in linkages.groupby("group_id"):
            assert group["site"].is_unique, f"{case}: group {group_id} repeats a site"
        assert_output_matches(linkage_path, EXPECTED_DIR / f"link_{case}.csv")

    def test_long_steps_say_how_to_stop(
        self,
        pipeline: Callable[[str], tuple[Path, list[Path], Path]],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A step that may take a while says how to stop it with Ctrl+C.

        Link also says roughly how long it will take. A short step says
        neither.
        """
        _, hashes, _ = pipeline("exact")

        def link(output: str) -> str:
            rc = _link.link(
                hashes=[h.name for h in hashes],
                threshold=CASES["exact"],
                output=output,
                sites=list(SITES),
                provenance=provenance(),
                **call_kwargs(hashes[0].parent, output_folder=tmp_path),
            )
            assert rc == 0
            return strip_ansi(capsys.readouterr().out)

        capsys.readouterr()
        short = link("short.pprl")
        assert not shows(msg.STOPPABLE, short)
        assert "pairs of records" not in short
        monkeypatch.setitem(_ui.STOPPABLE_FROM, "link", 1)
        long = link("long.pprl")
        assert shows(msg.STOPPABLE, long), long

    def test_parallel_linkage(
        self, pipeline: Callable[[str], tuple[Path, list[Path], Path]]
    ) -> None:
        """Linkage split across processes finds the same pairs, in the same order."""
        _, hashes, _ = pipeline("typo_heavy")
        clks = [
            [deserialize_bitarray(c) for c in members(h, "hashes")["hash"]]
            for h in hashes
        ]
        expected = anonlink.candidate_generation.find_candidate_pairs(
            clks, anonlink.similarities.dice_coefficient, 0.7
        )
        assert len(expected[0]) > 100, "The test needs many candidate pairs"
        sizes = [len(c) for c in clks]
        for workers, chunk in ((1, 10**9), (1, 1_000), (2, 1_000)):
            with _comparing(clks, workers, min_comparisons=0) as compare:
                found = _candidate_pairs(compare, sizes, 0.7, chunk_comparisons=chunk)
            case = f"{workers} worker(s), chunks of {chunk}"
            assert list(found[0]) == list(expected[0]), case
            assert [list(a) for a in found[1]] == [list(a) for a in expected[1]], case
            assert [list(a) for a in found[2]] == [list(a) for a in expected[2]], case

    def test_parallel_hashing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hashing in chunks, in one process or several, gives clkhash's own hashes.

        The hashes match, in the same order, what clkhash gives from the same
        rows written as CSV.
        """
        assert synthesize_patients(tmp_path, n=50) == 0
        hashed = {}

        def recorded(
            rows: pd.DataFrame,
            secret: str,
            schema: Schema,
            workers: int | None = None,
            chunk_rows: int = _hash.HASH_CHUNK_ROWS,
            *,
            record_salt: dict | None = None,
        ) -> list:
            hashed.update(rows=rows, secret=secret, schema=schema)
            return _hash_rows(
                rows, secret, schema, workers, chunk_rows, record_salt=record_salt
            )

        monkeypatch.setattr("pprl._hash._hash_rows", recorded)
        rc = _hash.hash_records(
            patients="site_a.clean.pprl",
            secret=SYNTH_SECRET,
            fields=SYNTH_FIELDS,
            output="site_a.hashes.pprl",
            **HASH_SIZES,
            provenance=provenance("site_a"),
            **call_kwargs(tmp_path),
        )
        assert rc == 0
        rows, secret, schema = hashed["rows"], hashed["secret"], hashed["schema"]
        # The index is the schema's first feature, which is unnamed and ignored.
        csv_text = rows.to_csv(index=True, lineterminator="\n")
        expected = clk.generate_clk_from_csv(
            io.StringIO(csv_text), secret, schema, progress_bar=False, max_workers=1
        )
        assert len(expected) == 50
        for workers in (1, 2):
            actual = _hash_rows(rows, secret, schema, workers=workers, chunk_rows=7)
            assert actual == expected, f"{workers} worker(s)"
        stored = members(tmp_path / "site_a.hashes.pprl", "hashes")["hash"]
        assert [deserialize_bitarray(c) for c in stored] == expected

    def test_workers_leave_ctrl_c(self) -> None:
        """Worker processes ignore Ctrl+C, so that only pprl reports it.

        The main process still receives it.
        """
        before = signal.getsignal(signal.SIGINT)
        with worker_pool(1) as pool:
            handler = pool.submit(signal.getsignal, signal.SIGINT).result()
            if hasattr(signal, "pthread_sigmask"):  # Not available on Windows
                blocked = pool.submit(signal.pthread_sigmask, signal.SIG_BLOCK, ())
                assert signal.SIGINT in blocked.result(), (
                    "Blocked from the start, before the worker imports pprl"
                )
        assert handler == signal.SIG_IGN
        if hasattr(signal, "pthread_sigmask"):
            assert signal.SIGINT not in signal.pthread_sigmask(signal.SIG_BLOCK, ())
        assert signal.getsignal(signal.SIGINT) == before

    def test_exact_columns(self, tmp_path: Path) -> None:
        """Every file holds exactly the columns its command writes.

        A hash file holds study_id and hash. A column that is not declared
        stops clean and hash, and nothing is written.
        """
        assert synthesize_patients(tmp_path, n=20, cleaned=False) == 0
        assert synthesize_patients(tmp_path, n=20) == 0
        rc = _hash.hash_records(
            patients="site_a.clean.pprl",
            secret=SYNTH_SECRET,
            fields=SYNTH_FIELDS,
            output="site_a.hashes.pprl",
            **HASH_SIZES,
            provenance=provenance("site_a"),
            **call_kwargs(tmp_path),
        )
        assert rc == 0
        hashes = members(tmp_path / "site_a.hashes.pprl", "hashes")
        assert list(hashes.columns) == ["study_id", "hash"]

        raw = read_csv_frame(tmp_path / "patients.csv")
        write_csv_frame(raw.assign(ward="7"), tmp_path / "extra.csv")
        write_cleaned(raw.assign(ward="7"), tmp_path / "extra.clean.pprl", "site_a")
        before = files_in(tmp_path)
        assert (
            _clean.clean(
                patients="extra.csv",
                id_column=SYNTH_ID,
                fields=SYNTH_FIELDS,
                output="new.clean.pprl",
                flagged="set_aside.csv",
                provenance=provenance("site_a"),
                **call_kwargs(tmp_path),
            )
            == 1
        ), "clean refuses an undeclared column"
        assert (
            _hash.hash_records(
                patients="extra.clean.pprl",
                secret=SYNTH_SECRET,
                fields=SYNTH_FIELDS,
                output="new.hashes.pprl",
                **HASH_SIZES,
                provenance=provenance("site_a"),
                **call_kwargs(tmp_path),
            )
            == 1
        ), "hash refuses an undeclared column"
        assert files_in(tmp_path) == before, "Nothing is written"

    def test_outputs_together(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A command writes all of its outputs or none.

        Clean, if stopped between its two files, leaves neither, so running it
        again works.
        """
        make_workspace(tmp_path)
        write_text(
            tmp_path / "patients.csv",
            "person_id,name,city,state,zip,dob\n1,Jo Last,Boston,MA,02101,1990-01-01\n",
        )
        scores = pd.DataFrame({SYNTH_ID: ["1"], "score": ["5"]})
        write_selected(tmp_path / "site_a.selected.pprl", "site_a", {"scores": scores})

        def broken(*_args: object, **_kwargs: object) -> Never:
            reason = "the drive went away"
            raise OSError(reason)

        with monkeypatch.context() as patched:
            patched.setattr("pprl._clean.write_csv_frame", broken)
            rc, output = run_in(tmp_path, ["clean"])
        assert rc == 1, output
        assert shows(msg.UNEXPECTED, output), output
        assert files_in(tmp_path) == [
            "outbox",
            "outbox/coordinator",
            "outbox/coordinator/site_a.public_key.pprl",
            "patients.csv",
            "pprl-workspace.yml",
            "site_a.log",
            "site_a.pprl",
            "site_a.private_key.pprl",
            "site_a.secret.pprl",
            "site_a.selected.pprl",
        ], "Neither output, nor any part of one, is left"
        rc, output = run_in(tmp_path, ["clean"])
        assert rc == 0, output

    def test_sweep_output(self, tmp_path: Path) -> None:
        """A grid sweep writes one row per threshold, with precision and recall.

        These are judged by synth's person_ids.
        """
        rc = run_scenario(tmp_path, two_site_scenario(shared=20), "sites")
        assert rc == 0, "synth failed"
        write_yaml(
            tmp_path / "sweep.yml",
            {
                "patients": [f"sites/{site}/patients.csv" for site in SITES],
                "id": SYNTH_ID,
                "fields": SYNTH_FIELDS,
                **HASH_SIZES,
                "thresholds": [0.90, 0.95],
                "output": "assessed.csv",
                "assessment": True,
            },
        )
        make_workspace(tmp_path, role="coordinator")
        rc, output = run_in(tmp_path, ["sweep"])
        assert rc == 0, output
        assessed = read_csv_frame(tmp_path / "assessed.csv")
        assert len(assessed) == 2, "Should have one row per threshold"
        assert {"threshold", "matches", "precision", "recall"} <= set(assessed.columns)
        assert (assessed[["precision", "recall"]].astype(float) == 1.0).all().all(), (
            "Identical records of one person should all be found, and nothing else"
        )
        assert list(assessed["weight_name"]) == ["500", "500"], "The weights as given"

    def test_sweep_of_folded_hashes(self, tmp_path: Path) -> None:
        """A grid sweep folds hashes as its xor_folds says.

        Folding lowers the hashes' density.
        """
        rc = run_scenario(
            tmp_path, two_site_scenario(shared=100, only_a=20, only_b=20), "sites"
        )
        assert rc == 0, "synth failed"
        make_workspace(tmp_path, role="coordinator")
        densities = []
        for folds in (0, 1):
            (tmp_path / "sweep.yml").unlink(missing_ok=True)
            write_yaml(
                tmp_path / "sweep.yml",
                {
                    "patients": [f"sites/{site}/patients.csv" for site in SITES],
                    "id": SYNTH_ID,
                    "fields": SYNTH_FIELDS,
                    **HASH_SIZES,
                    "xor_folds": folds,
                    "thresholds": [0.95],
                    "output": f"assessed_{folds}.csv",
                    "assessment": True,
                },
            )
            rc, output = run_in(tmp_path, ["sweep"])
            assert rc == 0, output
            assessed = read_csv_frame(tmp_path / f"assessed_{folds}.csv")
            densities.append(float(assessed["density_mean"][0]))
        assert densities[1] < densities[0] - 0.1, densities

    def test_sweep_of_salted_hashes(self, tmp_path: Path) -> None:
        """A grid sweep uses the project's salts.

        It shows its weights without logging them.
        """
        rc = run_scenario(
            tmp_path, two_site_scenario(shared=100, only_a=20, only_b=20), "sites"
        )
        assert rc == 0, "synth failed"
        make_workspace(tmp_path, role="coordinator")
        write_yaml(
            tmp_path / "sweep.yml",
            {
                "patients": [f"sites/{site}/patients.csv" for site in SITES],
                "id": SYNTH_ID,
                "fields": SYNTH_FIELDS,
                **HASH_SIZES,
                "project_salt": "a salt",
                "record_salt": {"field": "zip", "length": 3},
                "thresholds": [0.95],
                "output": "assessed.csv",
                "assessment": True,
            },
        )
        rc, output = run_in(tmp_path, ["sweep"])
        assert rc == 0, output
        assert "name=" in output, "The terminal shows the weights"
        log = read_text(log_of(tmp_path))
        assert "name=" not in log, log

    @pytest.mark.extended
    def test_example_scenarios(self, tmp_path: Path) -> None:
        """The example scenarios produce the same dataset every time."""
        for example, output_name in EXAMPLE_OUTPUTS.items():
            source = EXAMPLES_DIR / example
            if source.is_dir():
                shutil.copytree(source, tmp_path / example)
            else:
                shutil.copy(source, tmp_path / example)
            rc = synthesize(example, **call_kwargs(tmp_path))
            assert rc == 0, f"{example}: synth returned {rc}"
            assert_folder_matches(
                tmp_path / output_name, EXPECTED_DIR / "examples" / output_name
            )

    @pytest.mark.extended
    def test_generated_histories(self, tmp_path: Path) -> None:
        """Generated histories are split between sites as the scenario says."""

        def run(
            name: str, modules: Sequence[str] = HISTORY_MODULES, **overrides: object
        ) -> Path:
            rc = run_scenario(tmp_path, _history_scenario(modules, **overrides), name)
            assert rc == 0, f"{name}: synth returned {rc}"
            return tmp_path / name

        everywhere = run("everywhere")
        a, b = (_events(everywhere, site) for site in SITES)
        assert a == b, "By default, both sites should record the whole history"
        assert len({table for _, table, _, _ in a}) > 1, (
            "By default, both sites should record the whole history"
        )

        one_site = run("one_site", event_sharing={"one_site": 1})
        split = [_events(one_site, site) for site in SITES]
        assert sorted(split[0] + split[1]) == a, (
            "With one_site, each event should be recorded at exactly one site"
        )
        assert all(split), (
            "With one_site, each event should be recorded at exactly one site"
        )

        same_month = run("same_month", same_month=1)
        a, b = (_events(same_month, site) for site in SITES)
        months = [sorted((p, t, d[:7]) for p, t, d, _ in rows) for rows in (a, b)]
        assert months[0] == months[1], (
            "With same_month, the second site should record a redrawn copy "
            "of each event in the same month"
        )
        assert a != b, (
            "With same_month, the second site should record a redrawn copy "
            "of each event in the same month"
        )

        covered_modules = ("fobt", "fit_dna")
        covered = run("coverage", site_coverage={"site_b": list(covered_modules)})
        only_covered = run("only_covered", modules=covered_modules)
        assert _events(covered, "site_b") == _events(only_covered, "site_b"), (
            "A site should record only the modules in its site_coverage"
        )

        per_site = run("per_site", person_ids="per_site")
        population = read_csv_frame(per_site / "population.csv")
        assert (population["site_a"] != population["site_b"]).all(), (
            "With per_site person ids, no individual should have the same id twice"
        )
        for site in SITES:
            individual_of = dict(
                zip(population[site], population["individual_id"], strict=True)
            )
            assert _events(per_site, site, individual_of) == _events(
                everywhere, site
            ), f"per_site person ids should only relabel {site}'s records"
            patients = read_csv_frame(per_site / site / "patients.csv")
            assert list(patients["person_id"]) == sorted(population[site], key=int), (
                f"{site}'s patient file should use its own person ids, in order"
            )
        linkages = read_csv_frame(per_site / "true_linkages.csv")
        pairs = linkages.pivot(index="group_id", columns="site", values="person_id")  # noqa: PD010 (one value each)
        assert sorted(map(tuple, pairs[list(SITES)].values)) == sorted(
            map(tuple, population[list(SITES)].values)
        ), "true_linkages.csv should link each individual's records"

        added = run("added_module", modules=(*HISTORY_MODULES, "tobacco_status"))
        added_events = _events(added, "site_a")
        tobacco_only = _events(
            run("tobacco_only", modules=["tobacco_status"]), "site_a"
        )
        assert sorted(_events(everywhere, "site_a") + tobacco_only) == added_events, (
            "Adding a module should not change what other modules draw"
        )

        boston = yaml.safe_load(read_text(EXAMPLES_DIR / "boston" / "01_preamble.yml"))
        boston_ids = {row["name"]: row["id"] for row in boston["concept"]}
        assert {
            name: boston_ids[name] for name in MODULE_CONCEPTS if name in boston_ids
        } == {
            name: id_ for name, id_ in MODULE_CONCEPTS.items() if name in boston_ids
        }, "Module concepts should have the ids the Boston scenario gives them"

    def test_every_module_runs(self, tmp_path: Path) -> None:
        """Every clinical module draws the same histories each time.

        This runs in every test run, on a few individuals. The stored copies
        of full example datasets are compared only with every test.
        """
        modules = sorted(MODULES)
        scenario = _history_scenario(modules)
        scenario["generated_individuals"][0]["count"] = 12
        runs = []
        for name in ("first", "second"):
            assert run_scenario(tmp_path, scenario, name) == 0, f"{name}: synth failed"
            runs.append(tmp_path / name)
        files = sorted(p.relative_to(runs[0]) for p in runs[0].rglob("*.csv"))
        assert files == sorted(p.relative_to(runs[1]) for p in runs[1].rglob("*.csv"))
        for path in files:
            assert (runs[0] / path).read_bytes() == (runs[1] / path).read_bytes(), (
                f"{path} differs between two runs of one scenario"
            )
        tables = {table for _, table, _, _ in _events(runs[0], "site_a")}
        assert len(tables) > 3, f"Events in only {sorted(tables)}"

    def test_site_rows_and_person_locations(self, tmp_path: Path) -> None:
        """Shared rows that list their sites are written only at those sites.

        Each site also records every person's address as a location.
        """
        scenario = _history_scenario(
            modules=["routine_exam"],
            person_ids="per_site",
            person_locations=True,
            pii={"address": {"change_rate": 0.3, "typos": {"rate": 0.3}}},
            location=[{"source_value": "campus", "city": "Boston", "zip": "02118"}],
            care_site=[
                {"source_value": "a_only", "name": "A only", "sites": ["site_a"]},
                {"source_value": "everywhere", "name": "Everywhere"},
            ],
        )
        dataset = tmp_path / "rows"
        assert run_scenario(tmp_path, scenario, "rows") == 0, "synth failed"

        def omop(site: str, table: str) -> pd.DataFrame:
            return read_csv_frame(dataset / site / "OMOP" / f"{table}.csv")

        care_sites = {
            site: set(omop(site, "care_site")["care_site_id"]) for site in SITES
        }
        assert care_sites == {"site_a": {"1", "2"}, "site_b": {"2"}}, (
            "A care site listing its sites should be written only at those sites"
        )
        visited = {
            site: set(omop(site, "visit_occurrence")["care_site_id"]) - {""}
            for site in SITES
        }
        assert visited["site_a"] == {"1", "2"}, (
            "A site should record a visit at another site's care site "
            "without a care site"
        )
        assert visited["site_b"] == {"2"}, (
            "A site should record a visit at another site's care site "
            "without a care site"
        )

        for site in SITES:
            locations = omop(site, "location").set_index("location_id")
            person = omop(site, "person").set_index("person_id")
            patients = read_csv_frame(dataset / site / "patients.csv")
            assert "1" in locations.index, (
                f"{site}: persons' locations should be numbered after the scenario's"
            )
            assert person["location_id"].ne("1").all(), (
                f"{site}: persons' locations should be numbered after the scenario's"
            )
            recorded = locations.loc[
                person.loc[patients["person_id"], "location_id"],
                ["city", "state", "zip"],
            ]
            assert (
                recorded.to_numpy().tolist()
                == patients[["city", "state", "zip"]].to_numpy().tolist()
            ), f"{site}: each person's location should be the address the site records"


# A study's three tables, with its rules. The rules keep the patients
# living in a neighborhood of the city during the study's years, and the
# measures the study uses.
STUDY_TABLES = {
    "demographics": {
        "columns": ["person_id", "sex"],
        "primary_key": ["person_id"],
        "keep": [{"found_in": {"table": "residence", "on": ["person_id"]}}],
        "censor": ["sex"],
    },
    "residence": {
        "columns": ["person_id", "year", "neighborhood"],
        "primary_key": ["person_id", "year"],
        "keep": [
            {"not_empty": "neighborhood"},
            {"one_of": {"year": ["2023", "2024", "2025"]}},
        ],
        "censor": [],
    },
    "measures": {
        "columns": ["person_id", "year", "measure_id", "measure_name", "value"],
        "primary_key": ["person_id", "year", "measure_id"],
        "keep": [
            {"found_in": {"table": "residence", "on": ["person_id", "year"]}},
            {"one_of": {"measure_id": ["M01", "M03"]}},
        ],
        "censor": [],
    },
}


class TestSelection:
    """Pprl select, and what clean and pack keep after it."""

    def _site(self, folder: Path) -> None:
        """Make a site's workspace with the study's tables.

        Patients 1 to 4 are in the patient file. Patient 9 is only in the tables.
        """
        make_workspace(folder)
        write_bundle(folder, tables=STUDY_TABLES)
        tables = {
            "demographics": "person_id,sex\n1,F\n2,M\n3,F\n4,M\n",
            "residence": "person_id,year,neighborhood\n"
            "1,2023,Back Bay\n1,2022,Back Bay\n2,2024,\n"
            "3,2025,Roxbury\n9,2024,Fenway\n",
            "measures": "person_id,year,measure_id,measure_name,value\n"
            "1,2023,M01,Housing,1\n1,2023,M02,Heart,0\n"
            "1,2022,M01,Housing,1\n3,2025,M03,Mood,1\n2,2024,M03,Mood,0\n",
        }
        for name, text in tables.items():
            write_text(folder / "tables" / f"{name}.csv", text)
        write_text(
            folder / "patients.csv",
            "person_id,name,city,state,zip,dob\n"
            + "".join(
                f"{i},Pat {i},Boston,MA,02101,1990-01-0{i}\n" for i in range(1, 6)
            ),
        )

    def test_rules_in_order(self, tmp_path: Path) -> None:
        """Select applies each table's rules in order, and clean and pack follow it.

        Each table is selected after the tables its rules look in. Select
        reports what each rule left out and empties the censored column.
        Clean and pack keep only the study's patients and rows.
        """
        self._site(tmp_path)
        rc, output = run_in(tmp_path, ["select"])
        assert rc == 0, output
        for line in (
            "demographics: kept 2 of 4 rows.",
            "Left out 2 rows: residence has no row with the same person_id.",
            "Emptied sex in every row kept.",
            "residence: kept 3 of 5 rows.",
            "Left out 1 row: neighborhood is empty.",
            "Left out 1 row: year is not one of 3 listed values.",
            "measures: kept 2 of 5 rows.",
            "Left out 2 rows: residence has no row with the same person_id, year.",
            "Left out 1 row: measure_id is not one of 2 listed values.",
            "for 3 patients",
        ):
            assert line in output, line
        rc, output = run_in(tmp_path, ["clean"])
        assert rc == 0, output
        assert shows(msg.CLEAN_NOT_SELECTED, output)
        assert "Left out 3 rows" in output
        assert shows(msg.CLEAN_NO_PATIENT, output)
        assert "for 1 patient " in output
        study_ids = members(tmp_path / "site_a.clean.pprl", "study_ids")
        assert sorted(study_ids["person_id"]) == ["1", "3"], "Only the study's"
        rc, output = run_in(tmp_path, ["pack"])
        assert rc == 0, output
        assert "Left out 1 row of the table residence" in output, "Patient 9"
        packed = _pprl_file.read(
            tmp_path / "outbox" / "merge" / "site_a.tables.pprl"
        ).members
        person = dict(zip(study_ids["study_id"], study_ids["person_id"], strict=True))
        rows = {
            name: sorted(
                (person[r[STUDY_ID]], *r.drop(STUDY_ID)) for _, r in df.iterrows()
            )
            for name, df in packed.items()
        }
        assert rows == {
            "demographics": [("1", ""), ("3", "")],
            "residence": [("1", "2023", "Back Bay"), ("3", "2025", "Roxbury")],
            "measures": [
                ("1", "2023", "M01", "Housing", "1"),
                ("3", "2025", "M03", "Mood", "1"),
            ],
        }

    def test_pack_checks_again(self, tmp_path: Path) -> None:
        """Pack refuses rows that the rules would not have let select write.

        This holds however the rows got into the selected file. Nothing is
        written.
        """
        self._site(tmp_path)
        assert run_in(tmp_path, ["select"])[0] == 0
        assert run_in(tmp_path, ["clean"])[0] == 0
        path = tmp_path / "site_a.selected.pprl"
        selected = _pprl_file.read(path)
        tables = dict(selected.members)
        table = tables["demographics"]
        # The column is no longer censored.
        tables["demographics"] = table.assign(sex="F")
        path.unlink()
        _pprl_file.write(
            path,
            "selected",
            tables,
            provenance=selected.header,
        )
        clean = _pprl_file.read(tmp_path / "site_a.clean.pprl")
        (tmp_path / "site_a.clean.pprl").unlink()
        _pprl_file.write(
            tmp_path / "site_a.clean.pprl",
            "clean",
            clean.members,
            provenance=clean.header,
            inputs=[_pprl_file.read(path, lazy=True)],
        )
        rc, output = run_in(tmp_path, ["pack"])
        assert rc == 1, output
        assert shows(msg.PACK_NOT_SELECTED, output), output
        assert "2 rows of the table demographics" in output
        assert not (tmp_path / "outbox" / "merge" / "site_a.tables.pprl").exists()

    def test_order_of_tables(self) -> None:
        """A table is selected after every table its found_in rules look in.

        This holds whatever order the bundle lists them in.
        """
        assert in_order(STUDY_TABLES) == [
            "residence",
            "demographics",
            "measures",
        ]


def _hash_file(
    folder: Path, site: str, ranges: Iterable[tuple[int, int]], length: int = 1024
) -> str:
    """Make a hash file for ``site``, with one record per (first, last) range.

    Each record's hash has the bits in its range set. Two such hashes of 100
    bits each that share n bits have a Dice similarity of n / 100.
    """
    clks = []
    for first, last in ranges:
        bits = bitarray(length)
        bits.setall(0)
        bits[first : last + 1] = 1
        clks.append(serialize_bitarray(bits))
    hashes = pd.DataFrame(
        {"study_id": [str(i) for i in range(1, len(clks) + 1)], "hash": clks}
    )
    name = f"{site}.hashes.pprl"
    _pprl_file.write(
        folder / name,
        "hashes",
        {"hashes": hashes},
        provenance=provenance(site),
    )
    return name


def _groups(folder: Path, hashes: list[str], threshold: float) -> list:
    """List the groups pprl link finds, as sets of (site, study_id)."""
    rc = _link.link(
        hashes=hashes,
        threshold=threshold,
        output="linkages.pprl",
        provenance=provenance(),
        **call_kwargs(folder),
    )
    assert rc == 0
    linkages = members(folder / "linkages.pprl", "linkages")
    return sorted(
        sorted(zip(group["site"], group["study_id"], strict=True))
        for _, group in linkages.groupby("group_id")
    )


class TestLinkageRules:
    """How link groups records."""

    def test_one_record_per_site(self, tmp_path: Path) -> None:
        """A group never holds two records from one site.

        When two records from one site both match a third record, only the
        closer pair is grouped.
        """
        hashes = [
            _hash_file(tmp_path, "site_a", [(0, 99), (15, 114)]),  # 0.95, 0.90 to b
            _hash_file(tmp_path, "site_b", [(5, 104)]),
        ]
        assert _groups(tmp_path, hashes, 0.85) == [[("site_a", "1"), ("site_b", "1")]]

    def test_chains(self, tmp_path: Path) -> None:
        """With three sites, a chain of two matches is grouped, but not one of three.

        A group where 1 of 2 pairs is above the threshold is formed. A fourth
        record that matches only the third is not added (1 of 3 pairs).
        """
        # Neighbors share 80 of their 100 bits (0.8). Records two apart share
        # 60 (0.6), and records three apart share 40 (0.4).
        ranges = {"site_a": (0, 99), "site_b": (20, 119), "site_c": (40, 139)}
        hashes = [_hash_file(tmp_path, s, [r]) for s, r in ranges.items()]
        chain = [("site_a", "1"), ("site_b", "1"), ("site_c", "1")]
        assert _groups(tmp_path, hashes, 0.7) == [chain]

        (tmp_path / "linkages.pprl").unlink()
        hashes.append(_hash_file(tmp_path, "site_d", [(60, 159)]))
        assert _groups(tmp_path, hashes, 0.7) == [chain], "site_d stays apart"

    def test_chain_rule_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The chain rule is pprl's own setting, passed to the solver.

        The strict rule (every pair above the threshold) would split the chain.
        """
        ranges = {"site_a": (0, 99), "site_b": (20, 119), "site_c": (40, 139)}
        hashes = [_hash_file(tmp_path, s, [r]) for s, r in ranges.items()]
        monkeypatch.setattr("pprl._link.CHAIN_AGREEMENT", 1.0)
        assert _groups(tmp_path, hashes, 0.7) == [[("site_a", "1"), ("site_b", "1")]]
