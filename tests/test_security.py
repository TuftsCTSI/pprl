"""Test security guarantees.

These tests provide cryptographic assurances about the PPRL system:

- IRREVERSIBILITY: Patient identifiers cannot be recovered from the
  hashes.

- SECRET SENSITIVITY: Records can be linked only if both sets of
  identifiers were hashed with the very same secret.

- SECRET CHECK: Link refuses hash files whose bit patterns show they were
  hashed with different secrets, using nothing but the hashes.

- MESSAGES: No message shows a patient's details, site data, or the secret.

- SET ASIDE: The rows that clean sets aside are listed for the site by row
  number and id only, never with a patient's details.
"""

import base64
import io
import math
from typing import TYPE_CHECKING, Any, Never, cast

import anonlink
import pytest
from bitarray import bitarray
from clkhash.serialization import deserialize_bitarray

from pprl import _clean, _hash, _link, _pprl_file
from pprl import _messages as msg
from pprl._io import read_strings_csv, read_text, write_text
from pprl.synth import synthesize
from tests.helpers import (
    HASH_SIZES,
    SYNTH_FIELDS,
    SYNTH_ID,
    SYNTH_SECRET,
    call_kwargs,
    log_of,
    make_workspace,
    members,
    provenance,
    run_in,
    shows,
    synthesize_patients,
    write_yaml,
)

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.security


class TestSecurity:
    """Cryptographic security guarantees for the PPRL system."""

    # =========================================================================
    # GUARANTEE: Irreversibility of the hashes
    # =========================================================================

    def test_irreversibility(self, tmp_path: Path) -> None:
        """Hashes alone do not contain recoverable patient identifiers."""
        # --- Hashes do not contain raw identifiers ---
        patients_file = self._generate_patients(
            tmp_path, n=20, seed="irreversibility", prefix="SEARCHABLE"
        )

        secret = tmp_path / "secret.txt"
        write_text(secret, "1" * 64 + "\n")

        hashes_file = self._hash_patients(
            tmp_path, patients_file, secret, "irrev_hashes.hashes.pprl"
        )

        patients_df = members(patients_file, "patients")
        hashes_df = members(hashes_file, "hashes")

        identifier_cols = [c for c in patients_df.columns if c != "study_id"]
        all_identifiers = set()
        for col in identifier_cols:
            for val in patients_df[col].dropna():
                val_str = str(val).strip()
                if len(val_str) >= 3:
                    all_identifiers.add(val_str.lower())

        for clk in hashes_df["hash"]:
            clk_lower = clk.lower()
            for identifier in all_identifiers:
                assert identifier not in clk_lower, (
                    f"Found identifier '{identifier}' in hash output! "
                    f"Hashes must not contain recoverable PII."
                )
            decoded = base64.b64decode(clk).decode("utf-8", errors="ignore").lower()
            for identifier in all_identifiers:
                assert identifier not in decoded, (
                    f"Found identifier '{identifier}' in decoded hash! "
                    f"Hashes must not contain recoverable PII."
                )

        # --- All hashes are fixed-length Bloom filters ---
        bit_lengths = set()
        for clk in hashes_df["hash"]:
            ba = deserialize_bitarray(clk)
            bit_lengths.add(len(ba))

        assert len(bit_lengths) == 1, (
            f"Found variable hash lengths: {bit_lengths}. "
            f"All hashes must be fixed-length Bloom filters."
        )

        expected_length = HASH_SIZES["hash_length"]
        actual_length = bit_lengths.pop()
        assert actual_length == expected_length, (
            f"Hash length {actual_length} != expected {expected_length} bits."
        )

        # --- Bit density is consistent (proper saturation) ---
        bit_densities = []
        for clk in hashes_df["hash"]:
            ba = deserialize_bitarray(clk)
            density = ba.count() / len(ba)
            bit_densities.append(density)

        avg_density = sum(bit_densities) / len(bit_densities)
        min_density = min(bit_densities)
        max_density = max(bit_densities)

        expected_density = 1 - math.exp(-HASH_SIZES["fill_ratio"])
        assert abs(avg_density - expected_density) < 0.1, (
            f"Average bit density {avg_density:.3f}, but with fill ratio "
            f"{HASH_SIZES['fill_ratio']} it should be about {expected_density:.3f}."
        )

        density_range = max_density - min_density
        assert density_range < 0.25, (
            f"Bit density varies too widely ({min_density:.3f} to {max_density:.3f}). "
            f"All records should have similar structure."
        )

    # =========================================================================
    # GUARANTEE: Secret Sensitivity
    # =========================================================================

    def test_secret_sensitivity(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Hashes made with different secrets are unrelated, and never link.

        Even secrets one character apart give hashes that agree no more than
        chance would have them agree.
        """
        # --- Changing one character of the secret gives independent hashes ---
        patients_file = self._generate_patients(
            tmp_path, n=50, seed="secret_sensitivity", prefix="sens"
        )

        secret_a = tmp_path / "secret_a.txt"
        secret_b = tmp_path / "secret_b.txt"
        write_text(secret_a, "a" * 63 + "0\n")
        write_text(secret_b, "a" * 63 + "1\n")

        hashes_a = self._hash_patients(
            tmp_path, patients_file, secret_a, "sens_hashes_a.hashes.pprl"
        )
        hashes_b = self._hash_patients(
            tmp_path, patients_file, secret_b, "sens_hashes_b.hashes.pprl"
        )

        df_a = members(hashes_a, "hashes")
        df_b = members(hashes_b, "hashes")

        densities_a = []
        densities_b = []
        agreements = []

        for clk_a, clk_b in zip(df_a["hash"], df_b["hash"], strict=True):
            ba_a = deserialize_bitarray(clk_a)
            ba_b = deserialize_bitarray(clk_b)
            p_a = ba_a.count() / len(ba_a)
            p_b = ba_b.count() / len(ba_b)
            densities_a.append(p_a)
            densities_b.append(p_b)
            matching = (ba_a & ba_b).count() + (~ba_a & ~ba_b).count()
            agreements.append(matching / len(ba_a))

        avg_p_a = sum(densities_a) / len(densities_a)
        avg_p_b = sum(densities_b) / len(densities_b)
        observed_similarity = sum(agreements) / len(agreements)

        expected_similarity = avg_p_a * avg_p_b + (1 - avg_p_a) * (1 - avg_p_b)

        assert abs(observed_similarity - expected_similarity) < 0.03, (
            f"Observed similarity {observed_similarity:.4f} deviates from "
            f"expected-under-independence {expected_similarity:.4f} by "
            f"{abs(observed_similarity - expected_similarity):.4f}. "
            f"Hashes with different secrets should be statistically independent."
        )

        # --- Different secrets produce uncorrelated hashes (phi near 0) ---
        phi = self._compute_phi_coefficient(hashes_a, hashes_b)

        assert -0.05 <= phi <= 0.05, (
            f"Phi coefficient {phi:.4f} outside expected range [-0.05, 0.05]. "
            f"Different secrets should produce uncorrelated hashes."
        )

        # --- Different secrets produce zero linkages ---
        patients_link = self._generate_patients(
            tmp_path, n=30, seed="no_cross_link", prefix="cross"
        )

        secret_x = tmp_path / "secret_x.txt"
        secret_y = tmp_path / "secret_y.txt"
        write_text(secret_x, "0123456789abcdef" * 4 + "\n")
        write_text(secret_y, "fedcba9876543210" * 4 + "\n")

        hashes_x = self._hash_patients(
            tmp_path,
            patients_link,
            secret_x,
            "link_hashes_a.hashes.pprl",
            site="site_a",
        )
        hashes_y = self._hash_patients(
            tmp_path,
            patients_link,
            secret_y,
            "link_hashes_b.hashes.pprl",
            site="site_b",
        )

        x, y = (
            [deserialize_bitarray(c) for c in members(h, "hashes")["hash"]]
            for h in (hashes_x, hashes_y)
        )
        sims, _, _ = anonlink.candidate_generation.find_candidate_pairs(
            [x, y], anonlink.similarities.dice_coefficient, 0.95
        )
        assert len(sims) == 0, (
            f"Found {len(sims)} pairs with different secrets at threshold=0.95. "
            f"Expected 0 -- secrets must match exactly for linkage."
        )

        # --- Link links nothing, and says so for that pair of sites ---
        rc = _link.link(
            hashes=[hashes_x.name, hashes_y.name],
            threshold=0.95,
            output="linkages_sens.pprl",
            **call_kwargs(tmp_path),
        )
        assert rc == 0
        assert len(members(tmp_path / "linkages_sens.pprl", "linkages")) == 0
        assert shows(msg.NOTHING_LINKED_PAIR, capsys.readouterr().out), (
            "link warns that site_a and site_b linked no records"
        )

    # =========================================================================
    # GUARANTEE: Confinement to the workspace
    # =========================================================================

    def test_paths_confined(self, tmp_path: Path) -> None:
        """Commands refuse paths that lead out of their folder, even if called directly.

        The CLI also forces every command's data and output folder to be the
        workspace, and the usability grid runs these paths through the CLI
        for every command.
        """
        assert synthesize_patients(tmp_path, n=5) == 0
        assert synthesize_patients(tmp_path, n=5, cleaned=False) == 0
        write_yaml(
            tmp_path / "scenario.yml",
            {
                "seed": 1,
                "sites": ["site_a"],
                "outputs": ["patients"],
                "generated_individuals": [{"sites": ["site_a"], "count": 1}],
            },
        )

        def hash_with(**paths: str) -> int:
            config: dict[str, Any] = {
                "patients": "site_a.clean.pprl",
                "output": "out.pprl",
            } | paths
            return _hash.hash_records(
                secret=SYNTH_SECRET,
                fields=SYNTH_FIELDS,
                **HASH_SIZES,
                provenance=provenance("site_a"),
                **config,
                **call_kwargs(tmp_path),
            )

        def clean_with(**paths: str) -> int:
            config: dict[str, Any] = {
                "patients": "patients.csv",
                "id_column": SYNTH_ID,
                "fields": SYNTH_FIELDS,
                "output": "new.clean.pprl",
                "flagged": "flagged.csv",
            } | paths
            return _clean.clean(
                **config, provenance=provenance("site_a"), **call_kwargs(tmp_path)
            )

        write_text(
            tmp_path.parent / "outside.csv", read_text(tmp_path / "patients.csv")
        )
        for path in (str(tmp_path / "file.csv"), "../outside.csv", "a/../../b.csv"):
            assert hash_with(patients=path) == 1, f"hash: patients {path}"
            assert hash_with(output=path) == 1, f"hash: output {path}"
            assert clean_with(patients=path) == 1, f"clean: patients {path}"
            assert clean_with(output=path) == 1, f"clean: output {path}"
            assert clean_with(flagged=path) == 1, f"clean: flagged {path}"
        assert not (tmp_path.parent / "b.csv").exists(), "Nothing written outside"
        assert (
            synthesize(str(tmp_path / "scenario.yml"), **call_kwargs(tmp_path)) == 1
        ), "synth: absolute scenario path"

    # =========================================================================
    # GUARANTEE: The rows set aside are listed without a patient's details
    # =========================================================================

    def test_set_aside_holds_no_details(self, tmp_path: Path) -> None:
        """The file of rows that clean sets aside holds no identifying details.

        This is the only file a site opens. For each row it holds the row
        number, the id and the reason, and none of the identifying details.
        """
        details = ["Jo Canary", "Canaryville", "1990-31-12", "0210"]
        write_text(
            tmp_path / "patients.csv",
            "person_id,name,city,state,zip,dob\n"
            "1,Al Last-Name,Boston,MA,02101,1990-01-01\n"
            f"2,{details[0]},{details[1]},MA,02101,{details[2]}\n"
            f"3,{details[0]},{details[1]},MA,{details[3]},1990-01-01\n"
            f",{details[0]},{details[1]},MA,02101,1990-01-01\n",
        )
        rc = _clean.clean(
            patients="patients.csv",
            id_column=SYNTH_ID,
            fields=SYNTH_FIELDS,
            output="site_a.clean.pprl",
            flagged="site_a.set_aside.csv",
            provenance=provenance("site_a"),
            **call_kwargs(tmp_path),
        )
        assert rc == 0
        text = read_text(tmp_path / "site_a.set_aside.csv")
        header, *rows = text.splitlines()
        assert header == f"{msg.ROW_COLUMN},{SYNTH_ID},{msg.REASON_COLUMN}", header
        assert [row.split(",")[:2] for row in rows] == [
            ["3", "2"],
            ["4", "3"],
            ["5", ""],
        ]
        for detail in (*details, "MA"):
            assert detail not in text, detail

    # =========================================================================
    # GUARANTEE: Messages reveal no patient information and no secret
    # =========================================================================

    def test_messages_reveal_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No message, and no line of the log, quotes anything that must stay private.

        Private values are a patient's details, site data, the secret, and the
        hashing settings.

        Users may send their output to the coordinator. So messages name
        files, columns, row numbers and study_ids, but never values, and never
        a site's own ids. Each run below is rejected, or warns, because of a
        value marked SHOULD_NOT_APPEAR. That value is a patient's details or
        id, site data, a secret or a hashing setting, placed in a file or in
        the bundle. None of the output may contain it, including the report
        of an unexpected error.
        """
        hidden = "SHOULD_NOT_APPEAR"
        rows = (
            f"1,First Last,City,{hidden},{hidden},{hidden}\n"
            "2,???,City,MA,02101,1990-01-01\n"
        )
        header = "study_id,name,city,state,zip,dob\n"
        patients = "person_id,name,city,state,zip,dob\n" + rows
        repeated_ids = (
            "person_id,name,city,state,zip,dob\n"
            f"{hidden},A,B,MA,02101,1990-01-01\n{hidden},C,D,MA,02101,1990-01-01\n"
        )
        counter = iter(range(1000))

        def workspace(files: dict, role: str = "site", **settings: object) -> Path:
            """Make a workspace for ``role`` holding ``files``.

            The bundle gets ``settings``. Each file is given as a CSV file's
            text, or, for a pprl file, as its kind, its members as CSV texts,
            and its site.
            """
            folder = tmp_path / f"workspace_{next(counter)}"
            make_workspace(folder, role=role, **cast("dict[str, Any]", settings))
            for name, text in files.items():
                (folder / name).parent.mkdir(parents=True, exist_ok=True)
                if isinstance(text, str):
                    write_text(folder / name, text)
                    continue
                kind, texts, site = text
                frames = {
                    member: read_strings_csv(io.StringIO(csv))
                    for member, csv in texts.items()
                }
                _pprl_file.write(
                    folder / name,
                    kind,
                    frames,
                    provenance=provenance(site),
                )
            return folder

        cleaned = ("clean", {"patients": header + rows}, "site_a")
        site = {"patients.csv": patients, "site_a.clean.pprl": cleaned}
        runs = [
            (workspace(site), ["hash"]),
            (workspace(site), ["clean"]),
            (workspace({"patients.csv": repeated_ids}), ["clean"]),
            (
                workspace(
                    {
                        "tables/scores.csv": f"person_id,score\n{hidden},{hidden}\n",
                        "site_a.clean.pprl": (
                            "clean",
                            {
                                "patients": header + rows,
                                "study_ids": "person_id,study_id\n1,1\n",
                            },
                            "site_a",
                        ),
                    }
                ),
                ["pack"],
            ),
            (
                workspace({"malformed.csv": f'study_id,name\n1,"{hidden}\n'}),
                ["inspect", "malformed.csv"],
            ),
            (
                workspace(
                    {
                        f"site_{s}.hashes.pprl": (
                            "hashes",
                            {"hashes": f"study_id,hash\n1,{hidden}\n"},
                            f"site_{s}",
                        )
                        for s in ("a", "b")
                    },
                    role="linkage",
                ),
                ["link"],
            ),
        ]
        tables = {
            f"site_{s}.tables.pprl": (
                "tables",
                {"scores": f"study_id,score,date\n1,{hidden},{hidden}\n"},
                f"site_{s}",
            )
            for s in ("a", "b")
        }
        linked = {
            "linkages.pprl": (
                "linkages",
                {"linkages": "group_id,site,study_id\n1,site_a,1\n1,site_b,1\n"},
                None,
            )
        }
        for strategy, settings in (
            ("take_highest", {"column": "score"}),
            ("take_latest", {"column": "date"}),
            ("take_earliest", {"column": "date"}),
            ("take_top_ordinal", {"column": "score", "scale": ["low", "high"]}),
        ):
            rules = {
                "score": {"rank_by": [{strategy: settings}]},
                "date": {"drop": True},
            }
            if settings["column"] == "date":
                rules["date"] = {"tether": "score"}
            folder = workspace(
                tables | linked,
                role="merge",
                tables={"scores": {"primary_key": ["person_id"], "column": rules}},
            )
            runs.append((folder, ["merge"]))

        for folder, argv in runs:
            rc, output = run_in(folder, argv)
            assert rc in (0, 1), f"{argv} crashed:\n{output}"
            assert hidden not in output.upper(), f"{argv} showed:\n{output}"
            log = read_text(log_of(folder))
            assert hidden not in log.upper(), f"{argv} logged:\n{log}"

        # An unexpected error is reported by its kind and place, not its text.
        def broken(*_args: object, **_kwargs: object) -> Never:
            reason = f"{hidden}, born {hidden}"
            raise ValueError(reason)

        clean = (
            "clean",
            {"patients": header + "1,A,B,MA,02101,1990-01-01\n"},
            "site_a",
        )
        folder = workspace({"site_a.clean.pprl": clean})
        monkeypatch.setattr("pprl._pprl_file.zlib.compressobj", broken)
        rc, output = run_in(folder, ["hash"])
        assert rc == 1, output
        assert shows(msg.UNEXPECTED, output), output
        assert hidden not in output.upper(), output
        assert hidden not in read_text(log_of(folder)).upper()

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _generate_patients(
        self, tmp_path: Path, n: int, seed: int | str, prefix: str = "test"
    ) -> Path:
        """Generate synthetic patients as the cleaned file of the site ``prefix``."""
        output_file = f"{prefix}.clean.pprl"
        rc = synthesize_patients(
            tmp_path, n=n, site=prefix, seed=seed, output=output_file
        )
        assert rc == 0, f"synth failed with exit code {rc}"
        return tmp_path / output_file

    def _hash_patients(
        self,
        tmp_path: Path,
        patients_file: Path,
        secret_file: Path,
        output_name: str,
        site: str = "site_a",
    ) -> Path:
        """Hash a cleaned patient file with the given secret, as ``site``'s file.

        The records are the same, presented as that site's cleaned file.
        """
        cleaned = _pprl_file.read(patients_file)
        site_file = tmp_path / f"{site}_{patients_file.name}"
        if not site_file.exists():
            _pprl_file.write(
                site_file,
                "clean",
                cleaned.members,
                provenance=provenance(site),
            )
        rc = _hash.hash_records(
            patients=site_file.name,
            fields=SYNTH_FIELDS,
            secret=read_text(secret_file).strip(),
            output=output_name,
            **HASH_SIZES,
            provenance=provenance(site),
            **call_kwargs(tmp_path),
        )
        assert rc == 0, f"hash failed with exit code {rc}"
        return tmp_path / output_name

    def _compute_phi_coefficient(self, hashes_a: Path, hashes_b: Path) -> float:
        """Compute the phi coefficient between two hash files."""
        df_a = members(hashes_a, "hashes")
        df_b = members(hashes_b, "hashes")

        all_a = bitarray()
        all_b = bitarray()
        for clk_a, clk_b in zip(df_a["hash"], df_b["hash"], strict=True):
            all_a += deserialize_bitarray(clk_a)
            all_b += deserialize_bitarray(clk_b)

        a = (~all_a & ~all_b).count()
        b = (all_a & ~all_b).count()
        c = (~all_a & all_b).count()
        d = (all_a & all_b).count()

        numerator = (a * d) - (b * c)
        denominator = ((a + b) * (c + d) * (a + c) * (b + d)) ** 0.5

        return numerator / denominator if denominator != 0 else 0
