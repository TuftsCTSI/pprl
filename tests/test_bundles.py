"""Test bundles, and the commands on the project that write them.

The coordinator writes every bundle of a project with pprl bundles, in
their workspace. Each bundle holds only its role's settings, and none holds
the secret. A bundle is a pprl file without a checksum. pprl reads a bundle
only once it is sure the bundle is complete and meant for its release.
"""

import tomllib
from importlib.metadata import version
from typing import TYPE_CHECKING

import pandas as pd
import pytest
import yaml

from pprl import _bundle, _pprl_file, _workspace
from pprl import _messages as msg
from pprl._io import read_text, write_text
from pprl._shared import Role
from tests.helpers import (
    EXAMPLES,
    MERGE_TABLES,
    PROJECT_SETTINGS,
    SITE_TABLES,
    SOURCE_ROOT,
    SYNTH_FIELDS,
    coordinator_workspace,
    files_in,
    log_sections,
    make_workspace,
    rewrite_header,
    run_in,
    shows,
)
from tests.test_usability import style_problems

if TYPE_CHECKING:
    from pathlib import Path

    from pprl._workspace import Marker

SITES = ["site_a", "site_b", "site_c"]
PROJECT = PROJECT_SETTINGS | {"sites": SITES}


def _coordinator(folder: Path, **project: object) -> Marker:
    """Make the coordinator's workspace at ``folder``, for a project of three sites.

    The workspace holds project.yml and the bundles written from it.
    """
    return coordinator_workspace(folder, **PROJECT | project)


def _outbox(folder: Path) -> Path:
    return folder / "outbox"


def _bundle_path(folder: Path, name: str) -> Path:
    """Return where pprl bundles writes the bundle ``name``: outbox/<recipient>/."""
    return _outbox(folder) / name.removesuffix(".pprl") / name


@pytest.mark.security
class TestBundleContents:
    """What each role's bundle holds."""

    def test_each_role_its_own(self, tmp_path: Path) -> None:
        """Each bundle holds only its role's settings.

        None holds the secret, and no site's holds another site's name.
        """
        marker = _coordinator(tmp_path)
        bundles = {
            path.name: _bundle.read(path)
            for path in sorted(_outbox(tmp_path).glob("*/*.pprl"))
        }
        assert set(bundles) == {
            "site_a.pprl",
            "site_b.pprl",
            "site_c.pprl",
            "linkage.pprl",
            "merge.pprl",
        }
        assert not list(tmp_path.glob("*.pprl")), "Every bundle is in the outbox"
        common = {"project", "set", "release", "role"}
        assert len({b["set"] for b in bundles.values()}) == 1, "One bundle set"
        for name, bundle in bundles.items():
            assert bundle["project"] == {
                "id": marker.project,
                "name": PROJECT["name"],
            }, name
            assert bundle["release"] == version("pprl"), name
            assert set(bundle) - common == set(_bundle.ROLE_KEYS[bundle["role"]]), name
        site_bundles = [b for b in bundles.values() if b["role"] == "site"]
        assert [b["site"] for b in site_bundles] == ["site_a", "site_b", "site_c"]
        for name in bundles:
            data = _pprl_file.read(_bundle_path(tmp_path, name)).members["settings"]
            assert "secret" not in data, f"{name} has no secret"
        assert bundles["linkage.pprl"]["threshold"] == 0.95
        assert bundles["linkage.pprl"]["secret_check"] is True
        assert bundles["merge.pprl"]["sites"] == ["site_a", "site_b", "site_c"]
        assert bundles["merge.pprl"]["tables"] == MERGE_TABLES
        assert all(b["id"] == "person_id" for b in site_bundles)
        assert bundles["merge.pprl"]["id"] == "person_id"
        assert all(b["tables"] == SITE_TABLES for b in site_bundles), (
            "Each site's bundle gives the columns, primary key and "
            "selection rules of each table it sends"
        )
        assert all(b["xor_folds"] == 0 for b in site_bundles), (
            "Hashes are not folded unless project.yml says so"
        )
        assert all(b["fields"] == SYNTH_FIELDS for b in site_bundles), (
            "The fields' weights, as project.yml gives them"
        )

    def test_hash_sizes_to_sites_only(self, tmp_path: Path) -> None:
        """project.yml's hash length and fill ratio reach each site, and only them."""
        _coordinator(tmp_path, hash_length=512, fill_ratio=1.5)
        for path in _outbox(tmp_path).glob("*/*.pprl"):
            bundle = _bundle.read(path)
            site = bundle["role"] == "site"
            assert bundle.get("hash_length") == (512 if site else None), path.name
            assert bundle.get("fill_ratio") == (1.5 if site else None), path.name

    def test_xor_folds_to_sites_only(self, tmp_path: Path) -> None:
        """project.yml's xor_folds reaches each site, since only sites hash."""
        _coordinator(tmp_path, xor_folds=3)
        for path in _outbox(tmp_path).glob("*/*.pprl"):
            bundle = _bundle.read(path)
            assert bundle.get("xor_folds") == (
                3 if bundle["role"] == "site" else None
            ), path.name

    def test_salts_to_sites_only(self, tmp_path: Path) -> None:
        """project.yml's salts reach each site, since only sites hash.

        A project without salts has none in any bundle.
        """
        salts = {
            "project_salt": "a salt",
            "record_salt": {"field": "zip", "length": 3},
        }
        _coordinator(tmp_path / "salted", **salts)
        _coordinator(tmp_path / "plain")
        for project, given in (("salted", salts), ("plain", {})):
            for path in _outbox(tmp_path / project).glob("*/*.pprl"):
                bundle = _bundle.read(path)
                site = bundle["role"] == "site"
                for key in salts:
                    if site:
                        assert key in bundle, (project, path.name, key)
                    expected = given.get(key) if site else None
                    assert bundle.get(key) == expected, (project, path.name, key)

    def test_secret_check_to_linkage_agent_only(self, tmp_path: Path) -> None:
        """project.yml's secret_check reaches only the linkage agent, which runs link.

        It is set on its own, whatever the project's salts.
        """
        _coordinator(tmp_path, secret_check=False)
        for path in _outbox(tmp_path).glob("*/*.pprl"):
            bundle = _bundle.read(path)
            linkage = bundle["role"] == "linkage"
            assert bundle.get("secret_check") == (False if linkage else None), path
            if linkage:
                assert set(bundle) & set(_bundle.HASHING_KEYS) == set(), (
                    "The linkage agent learns nothing about hashing"
                )

    def test_selection_rules_to_sites_only(self, tmp_path: Path) -> None:
        """Every site gets the same rules for pprl select to apply.

        The merge agent never gets them. Its bundle holds only how to merge.
        """
        rules = {
            "keep": [{"not_empty": "score"}, {"one_of": {"score": ["5", "7"]}}],
            "censor": [],
        }
        _coordinator(tmp_path, tables={"scores": MERGE_TABLES["scores"] | rules})
        sites = [_bundle.read(_bundle_path(tmp_path, f"{site}.pprl")) for site in SITES]
        expected = SITE_TABLES["scores"] | rules
        assert all(b["tables"] == {"scores": expected} for b in sites)
        merge = _bundle.read(_bundle_path(tmp_path, "merge.pprl"))
        assert merge["tables"] == MERGE_TABLES, "No selection rule"

    def test_nothing_derived_from_the_secret(self, tmp_path: Path) -> None:
        """No bundle, and no workspace made from one, holds anything from the secret.

        Neither holds a checksum or any other value computed from the secret.
        The bundle's header has no checksum, and the marker records the
        bundle's set id.
        """
        _coordinator(tmp_path)
        path = _bundle_path(tmp_path, "site_a.pprl")
        header = _pprl_file.read_header(path)
        assert not [key for key in header if "sum" in key or "hash" in key], header
        assert set(_pprl_file.read(path).members) == {"settings"}
        study = tmp_path / "study"
        make_workspace(study)
        marker = _workspace.read(study)
        assert marker.bundle_set == _bundle.read(study / "site_a.pprl")["set"]

    def test_new_set_each_run(self, tmp_path: Path) -> None:
        """Each run of pprl bundles writes a new bundle set, even for the same project.

        This lets pprl tell a stale bundle from a current one.
        """
        _coordinator(tmp_path)
        first = _bundle.read(_bundle_path(tmp_path, "site_a.pprl"))
        for path in _outbox(tmp_path).glob("*/*.pprl"):
            path.unlink()
        rc, output = run_in(tmp_path, ["bundles"])
        assert rc == 0, output
        second = _bundle.read(_bundle_path(tmp_path, "site_a.pprl"))
        assert first["project"] == second["project"], "The same project"
        assert first["set"] != second["set"], "A new set"


@pytest.mark.reliability
class TestReadingBundles:
    """Reading a bundle, either as it was written or after a change."""

    def test_saved_again(self, tmp_path: Path) -> None:
        """A bundle re-saved in an editor never turns into different settings.

        If it is saved as text with other line endings, it is refused as
        damaged. If the editor changed only its readable first line, it is
        read as it was.
        """
        _coordinator(tmp_path)
        for name in ("site_a", "site_b", "site_c", "linkage", "merge"):
            path = _bundle_path(tmp_path, f"{name}.pprl")
            expected = _bundle.read(path)
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
            try:
                read = _bundle.read(path)
            except _bundle.BundleError as problem:
                read = str(problem)
            assert read in (expected, msg.BUNDLE_REASONS["not a bundle"]()), name
        path = _bundle_path(tmp_path, "site_a.pprl")
        path.write_bytes(path.read_bytes()[:-1] + bytes([path.read_bytes()[-1] ^ 1]))
        with pytest.raises(_bundle.BundleError) as problem:
            _bundle.read(path)
        assert str(problem.value) == msg.BUNDLE_REASONS["not a bundle"](), (
            "A changed byte"
        )

    def test_other_release(self, tmp_path: Path) -> None:
        """A bundle for another release is refused.

        A different release may hash differently.
        """
        _coordinator(tmp_path)
        path = _bundle_path(tmp_path, "site_a.pprl")
        rewrite_header(path, release="0.0.1")
        with pytest.raises(_bundle.BundleError) as problem:
            _bundle.read(path)
        assert str(problem.value) == msg.BUNDLE_REASONS["release"](
            release="0.0.1", version=version("pprl")
        )

    def test_not_a_bundle(self, tmp_path: Path) -> None:
        """A file that is not a bundle is refused, however it differs from one."""
        _coordinator(tmp_path)
        valid = _bundle.read(_bundle_path(tmp_path, "site_a.pprl"))
        path = tmp_path / "other.pprl"

        def written(contents: dict) -> bytes:
            path.unlink(missing_ok=True)
            _bundle.write(path, contents)
            return path.read_bytes()

        without_fields = {k: v for k, v in valid.items() if k != "fields"}
        of_site_a = {
            "project": valid["project"]["id"],
            "set": valid["set"],
            "site": "site_a",
        }
        cases = {
            "a missing setting": lambda: _pprl_file.write(
                path,
                "bundle",
                {
                    "settings": {
                        "name": "x",
                        **{
                            k: v
                            for k, v in without_fields.items()
                            if k in _bundle.ROLE_KEYS[Role.SITE]
                        },
                    }
                },
                provenance=of_site_a,
                role="site",
            ),
            "a hash file": lambda: _pprl_file.write(
                path,
                "hashes",
                {"hashes": pd.DataFrame({"study_id": ["1"], "hash": ["AA=="]})},
                provenance=of_site_a,
            ),
        }
        for what, make in cases.items():
            path.unlink(missing_ok=True)
            make()
            with pytest.raises(_bundle.BundleError) as problem:
                _bundle.read(path)
            assert str(problem.value) == msg.BUNDLE_REASONS["not a bundle"](), what
        for what, data in {
            "a YAML bundle, as earlier releases wrote": b"role: site\n",
            "empty": b"",
            "cut short": written(valid)[:-10],
        }.items():
            path.write_bytes(data)
            with pytest.raises(_bundle.BundleError) as problem:
                _bundle.read(path)
            assert str(problem.value) == msg.BUNDLE_REASONS["not a bundle"](), what
        with pytest.raises(FileNotFoundError):
            _bundle.read(tmp_path / "missing.pprl")


@pytest.mark.reliability
class TestProjectCommands:
    """The commands on the project: init, bundles and the others."""

    def test_init(self, tmp_path: Path) -> None:
        """Pprl init next to project.yml, with no bundle, makes a coordinator workspace.

        The workspace is for a new project, and the run is logged there.
        """
        for name in ("one", "two"):
            folder = tmp_path / name
            folder.mkdir()
            write_text(folder / "project.yml", "name: x\n")
            rc, output = run_in(folder, ["init"])
            assert rc == 0, output
            assert shows(msg.INIT_COORDINATOR_CREATED, output), output
            marker = _workspace.read(folder)
            assert marker.role == "coordinator"
            assert marker.site is None
            [(header, _, body, footer)] = log_sections(folder)
            assert header.endswith(" · coordinator · pprl init =====")
            assert body == output
            assert footer == "----- exit code 0 -----"
        projects = {_workspace.read(tmp_path / f).project for f in ("one", "two")}
        assert len(projects) == 2, "Each coordinator's workspace is a new project"

    def test_init_with_neither(self, tmp_path: Path) -> None:
        """Pprl init in a folder with no bundle and no project.yml creates nothing."""
        rc, output = run_in(tmp_path, ["init"])
        assert rc == 1, output
        assert shows(msg.INIT_NO_BUNDLE, output), output
        assert not style_problems(output, rejected=True), output
        assert files_in(tmp_path) == []

    def test_no_file_argument(self, tmp_path: Path) -> None:
        """Synth and sweep take no file: they read synth.yml and sweep.yml."""
        make_workspace(tmp_path, role="coordinator")
        for command in ("synth", "sweep"):
            rc, output = run_in(tmp_path, [command, f"{command}.yml"])
            assert rc == 1, output
            assert shows(msg.EXTRA_ARGUMENTS, output), output

    def test_outside_workspace(self, tmp_path: Path) -> None:
        """The commands on the project are refused outside a workspace."""
        for command in ("synth", "sweep", "bundles", "guides"):
            rc, output = run_in(tmp_path, [command])
            assert rc == 1, output
            assert shows(msg.NOT_A_WORKSPACE, output), output

    def test_help(self, tmp_path: Path) -> None:
        """Pprl on its own lists the commands on the project, with the others."""
        rc, output = run_in(tmp_path, [])
        assert rc == 1, output
        for command in ("synth", "sweep", "bundles", "guides", "review"):
            assert f"\n  {command}" in output, output

    def test_example_project(self, tmp_path: Path) -> None:
        """Pprl bundles accepts the example project.yml.

        CI makes its test bundles from it.
        """
        make_workspace(tmp_path, role="coordinator")
        example = EXAMPLES / "project.yml"
        write_text(tmp_path / "project.yml", read_text(example))
        rc, output = run_in(tmp_path, ["bundles"])
        assert rc == 0, output
        assert (_bundle_path(tmp_path, "merge.pprl")).exists()
        assert (_bundle_path(tmp_path, "site_b.pprl")).exists()

    def test_rules_as_written(self, tmp_path: Path) -> None:
        """Rules written unquoted, as the reference doc shows them, are read correctly.

        `on` (found_in's key) and `no` (a one_of value) are never read as
        booleans.
        """
        make_workspace(tmp_path, role="coordinator")
        tables = """
tables:
  visits:
    primary_key: [person_id]
    keep:
      - one_of: {smoker: [yes, no]}
    column:
      smoker: {rank_by: [{take_first_site: {}}]}
  scores:
    primary_key: [person_id]
    keep:
      - found_in: {table: visits, on: [person_id]}
    column:
      score: {rank_by: [{take_highest: {column: score}}]}
"""
        project = yaml.safe_dump({k: v for k, v in PROJECT.items() if k != "tables"})
        write_text(tmp_path / "project.yml", project + tables)
        rc, output = run_in(tmp_path, ["bundles"])
        assert rc == 0, output
        site = _bundle.read(_bundle_path(tmp_path, "site_a.pprl"))
        assert site["tables"]["visits"]["keep"] == [
            {"one_of": {"smoker": ["yes", "no"]}}
        ]
        assert site["tables"]["scores"]["keep"] == [
            {"found_in": {"table": "visits", "on": ["person_id"]}}
        ]

    def test_templates_in_the_wheel(self) -> None:
        """The wheel holds the guides' templates, which pprl guides needs."""
        config = tomllib.loads(read_text(SOURCE_ROOT / "pyproject.toml"))
        data = config["tool"]["setuptools"]["package-data"]["pprl"]
        assert "templates/*.html" in data, data
        assert list((SOURCE_ROOT / "src" / "pprl" / "templates").glob("*.html"))
