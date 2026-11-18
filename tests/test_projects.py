"""Test a whole project, from start to finish.

Each role works in its own workspace. The coordinator creates their
workspace, makes test data and writes the bundles. Every other role creates
its workspace from its bundle with pprl init, runs its commands, and sends
its files to the role that receives them. Every workspace is destroyed at
the end. Files move exactly as the transfers table says, and no workspace
ever holds anything its role must not have.
"""

import json
import shutil
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from pprl import _bundle, _locking, _pprl_file
from pprl._commands import COMMANDS as ROLE_COMMANDS
from pprl._config import read_config
from pprl._guides import DEPLOYMENT_FILE
from pprl._io import (
    read_csv_frame,
    read_text,
    write_csv_frame,
)
from pprl._merge import column_rules, merged_columns
from pprl._shared import Member
from pprl._workspace import read as read_workspace
from tests.commands import DESTROY_YES
from tests.helpers import (
    EXAMPLES,
    MERGE_TABLES,
    PROJECT_SETTINGS,
    RECEIPT_GLOB,
    log_of,
    run_in,
    write_yaml,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.security

SITES = ["site_a", "site_b", "site_c"]


# A receipt must be small enough to email, even with the log of many runs.
RECEIPT_AT_MOST = 100 * 1024


class Project:
    """A project in progress: its folder, and every transfer so far."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.sent = folder / "sent"
        self.transfers = set()  # (file, from, to)

    def run(self, where: Path, argv: list[str], answers: str = "") -> str:
        """Run pprl in ``where`` and return its output."""
        rc, output = run_in(where, argv, answers)
        assert rc == 0, f"{argv} in {where.name}:\n{output}"
        return output

    def upload(self, sender: str, path: Path, recipient: str) -> None:
        """Upload the file or folder at ``path``, from ``sender`` to ``recipient``.

        Every file is sent from the sender's outbox, in the recipient's folder
        there. No log is ever sent.
        """
        assert path.parent.parent.name == "outbox", f"{path} is not in an outbox"
        assert path.parent.name == recipient, f"{path} is not for {recipient}"
        box = self.sent / recipient
        box.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, box / path.name)
        else:
            shutil.copy(path, box / path.name)
        self.transfers.add((path.name, sender, recipient))

    def download(self, recipient: str, name: str, into: Path) -> None:
        """Have ``recipient`` download ``name`` into ``into``."""
        into.mkdir(parents=True, exist_ok=True)
        source = self.sent / recipient / name
        if source.is_dir():
            shutil.copytree(source, into / name)
        else:
            shutil.copy(source, into / name)

    def send(self, sender: str, path: Path, folder: Path) -> None:
        """Send the file at ``path``, from ``sender``, over a secure channel.

        The file never goes straight into the recipient's folder. The
        recipient saves it to ``folder`` themselves.
        """
        shutil.copy(path, folder / path.name)
        self.transfers.add((path.name, sender, folder.name))

    def create(self, role_name: str, bundle: str) -> Path:
        """Create ``role_name``'s workspace from its bundle, with pprl init.

        Create a new folder, download the bundle into it, and run pprl init there.
        """
        workspace = self.folder / role_name
        self.download(role_name, bundle, workspace)
        self.run(workspace, ["init"])
        (self.sent / role_name / bundle).unlink()  # The uploader deletes its copy.
        return workspace

    def finish(self, role_name: str) -> None:
        """Finish ``role_name``'s part: destroy its workspace, and send the receipt.

        First run each of the role's commands 3 more times; each is refused
        because its outputs exist, which makes the log as long as a real
        project's. Then destroy the workspace and send the receipt, which
        holds the log and must be small enough to email.
        """
        workspace = self.folder / role_name
        role = read_workspace(workspace).role
        for name, command in ROLE_COMMANDS.items():
            if command.role == role:
                for _ in range(3):
                    run_in(workspace, [name])
        log = read_text(log_of(workspace))
        self.run(workspace, ["destroy"], DESTROY_YES)
        [receipt] = workspace.glob(RECEIPT_GLOB)
        assert receipt.name.startswith(f"{role_name}.receipt_"), receipt.name
        assert read_text(receipt).endswith(log), "The receipt holds the log"
        assert receipt.stat().st_size < RECEIPT_AT_MOST, receipt.stat().st_size
        self.upload(role_name, receipt, "coordinator")


def _bundle_of(who: str) -> str:
    """Return the bundle name for ``who``, which is a site or an agent's role."""
    if who in ("linkage", "merge"):
        return _bundle.bundle_name(who)
    return _bundle.bundle_name("site", who)


def _coordinator(project: Project) -> Path:
    """Set up the coordinator's workspace, test data, and every bundle.

    Every bundle is uploaded.
    """
    coordinator = project.folder / "coordinator"
    coordinator.mkdir()
    write_yaml(coordinator / "project.yml", PROJECT_SETTINGS | {"sites": SITES})
    project.run(coordinator, ["init"])
    scenario = {
        "seed": "project",
        "sites": SITES,
        "outputs": ["patients"],
        "generated_individuals": [
            {"sites": SITES, "count": 12},
            *({"sites": [site], "count": 4} for site in SITES),
        ],
    }
    write_yaml(coordinator / "synth.yml", scenario)
    project.run(coordinator, ["synth"])
    project.run(coordinator, ["bundles"])
    deployment = read_config(EXAMPLES / DEPLOYMENT_FILE)
    deployment["contacts"] |= {
        site: {"name": f"{site} Example", "email": f"{site}@example.org"}
        for site in SITES
    }
    write_yaml(coordinator / DEPLOYMENT_FILE, deployment)
    project.run(coordinator, ["guides"])
    outbox = coordinator / "outbox"
    for who in (*SITES, "linkage", "merge"):
        project.upload("coordinator", outbox / who / _bundle_of(who), who)
        project.upload("coordinator", outbox / who / f"{who}.guide.html", who)
    project.upload("coordinator", outbox / "host" / "host.guide.html", "host")
    guides = [*outbox.glob("*/*.guide.html"), coordinator / "coordinator.guide.html"]
    assert len(guides) == len(SITES) + 4, "A guide for every role"
    return coordinator


def _key_exchange(project: Project, coordinator: Path) -> str:
    """Make each site's workspace, and exchange its key for its secret file.

    Each site participant sends the site's public key to the coordinator, who
    replies with the site's secret file. Return the secret, as the sites unlock it.
    """
    workspaces = {site: project.create(site, _bundle_of(site)) for site in SITES}
    for site, workspace in workspaces.items():
        project.send(
            site,
            workspace / "outbox" / "coordinator" / f"{site}.public_key.pprl",
            coordinator,
        )
    project.run(coordinator, ["secrets"])
    secrets = set()
    for site, workspace in workspaces.items():
        name = f"{site}.secret.pprl"
        project.send("coordinator", coordinator / "outbox" / site / name, workspace)
        secrets.add(
            _locking.unlock(
                _pprl_file.read(workspace / name),
                _pprl_file.read(workspace / f"{site}.private_key.pprl"),
            )
        )
    assert len(secrets) == 1, "One secret"
    [secret] = secrets
    return secret


# The prefix of every site's own person_ids in the exports. It makes them easy
# to tell apart from the study_ids that replace them: a study_id is a number,
# and a hyphen never appears in a hash (base64), a name or a setting.
SITE_ID = "local-"


def _sites(project: Project, coordinator: Path) -> set:
    """Run each site's commands, and upload its hashes and packed table.

    The site's exports (its patient file and its scores table, keyed by its
    own ids) are selected, cleaned, hashed and packed.
    """
    names = set()
    for site in SITES:
        workspace = project.folder / site
        # The files the site's data team made
        patients = read_csv_frame(coordinator / "synth" / site / "patients.csv")
        patients["person_id"] = SITE_ID + patients["person_id"]
        write_csv_frame(patients, workspace / "patients.csv")
        names |= set(patients["name"])
        table = patients[["person_id"]].copy()
        table["score"] = [str(len(name)) for name in patients["name"]]
        (workspace / "tables").mkdir()
        write_csv_frame(table, workspace / "tables" / "scores.csv")

        project.run(workspace, ["select"])
        project.run(workspace, ["clean"])
        project.run(workspace, ["hash"])
        project.run(workspace, ["pack"])
        project.upload(
            site, workspace / "outbox" / "linkage" / f"{site}.hashes.pprl", "linkage"
        )
        project.upload(
            site, workspace / "outbox" / "merge" / f"{site}.tables.pprl", "merge"
        )
    return names


def _linked_truly(project: Project, linkage: Path) -> None:
    """Check that link grouped each shared person's records, and nothing else.

    Clean numbers each site's patients 1 to N. Each group holds one record
    from every site, all of one person.
    """
    linkages = _pprl_file.read(linkage / "outbox" / "merge" / "linkages.pprl")
    person_of = {}
    for site in SITES:
        clean = _pprl_file.read(project.folder / site / f"{site}.clean.pprl")
        study_ids = clean.members[Member.STUDY_IDS]
        assert sorted(study_ids["study_id"], key=int) == [
            str(i) for i in range(1, len(study_ids) + 1)
        ], f"{site}'s study_ids are 1 to N"
        person_of[site] = dict(
            zip(study_ids["study_id"], study_ids["person_id"], strict=True)
        )
    groups = [
        [
            person_of[site][study_id]
            for site, study_id in zip(group["site"], group["study_id"], strict=True)
        ]
        for _, group in linkages.members[Member.LINKAGES].groupby("group_id")
    ]
    assert len(groups) == 12, "The 12 people at every site, and only they, link"
    assert all(len(group) == len(SITES) for group in groups), "One record a site"
    assert all(len(set(group)) == 1 for group in groups), "Each group is one person"


def _separation(project: Project, secret: str, names: set) -> None:
    """Check that no workspace holds anything its role must not have.

    - The linkage agent's never holds the secret, patient data or clinical
      tables.
    - The merge agent's never holds hashes, the secret or patient data.
    - The coordinator's never holds the secret.
    - No site's holds another site's files.
    """

    def texts(workspace: Path) -> dict[str, str]:
        """Return each file's text, decompressing pprl files' headers and members."""
        found = {}
        for p in workspace.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix == ".pprl":
                pprl_file = _pprl_file.read(p)
                parts = [json.dumps(pprl_file.header)] + [
                    m.to_csv() if isinstance(m, pd.DataFrame) else json.dumps(m)
                    for m in pprl_file.members.values()
                ]
                found[p.relative_to(workspace).as_posix()] = "\n".join(parts)
            else:
                text = p.read_bytes().decode("utf-8", errors="replace")
                found[p.relative_to(workspace).as_posix()] = text
        return found

    linkage = texts(project.folder / "linkage")
    assert not any(secret in text for text in linkage.values()), "No secret"
    assert not any(SITE_ID in text for text in linkage.values()), "No site's ids"
    assert not any(n in text for n in names for text in linkage.values()), "No names"
    assert not any(".tables." in name for name in linkage), "No site's tables"
    merged = texts(project.folder / "merge")
    assert not any(secret in text for text in merged.values()), "No secret"
    assert not any(".hashes." in name for name in merged), "No hashes"
    assert not any(n in text for n in names for text in merged.values()), "No names"
    assert not any(SITE_ID in text for text in merged.values()), "No site's ids"
    coordinator = texts(project.folder / "coordinator")
    assert not any(secret in text for text in coordinator.values()), "No secret"
    for site in SITES:
        others = [s for s in SITES if s != site]
        held = texts(project.folder / site)
        assert not any(name.startswith(tuple(others)) for name in held), site


class TestProjects:
    """Whole projects, each role in its own workspace."""

    def test_project(self, tmp_path: Path) -> None:
        """A project, with each role doing its part, ends with only the merged dataset.

        The linkage agent links the sites' hashes, and the merge agent merges
        their tables. The host receives only the merged dataset, with new ids
        and none of the sites' ids.
        """
        project = Project(tmp_path)
        coordinator = _coordinator(project)
        secret = _key_exchange(project, coordinator)
        names = _sites(project, coordinator)

        linkage = project.create("linkage", "linkage.pprl")
        for site in SITES:
            project.download("linkage", f"{site}.hashes.pprl", linkage)
        project.run(linkage, ["link"])
        _linked_truly(project, linkage)
        project.upload(
            "linkage", linkage / "outbox" / "merge" / "linkages.pprl", "merge"
        )
        project.upload(
            "linkage",
            linkage / "outbox" / "coordinator" / "linkage_report.pprl",
            "coordinator",
        )
        project.download("coordinator", "linkage_report.pprl", coordinator)
        project.run(coordinator, ["review"])
        assert (coordinator / "linkage_report.svg").is_file(), "The histogram"

        merge = project.create("merge", "merge.pprl")
        project.download("merge", "linkages.pprl", merge)
        for site in SITES:
            project.download("merge", f"{site}.tables.pprl", merge)
        project.run(merge, ["merge"])
        project.upload("merge", merge / "outbox" / "host" / "merged", "host")
        _separation(project, secret, names)

        hosted = sorted(p.name for p in (project.sent / "host").iterdir())
        assert hosted == ["host.guide.html", "merged"], (
            "The host receives only its guide and merged/"
        )
        scores = read_csv_frame(project.sent / "host" / "merged" / "scores.csv")
        assert list(scores.columns) == ["person_id", "score"], "No site's details"
        for table, rules in MERGE_TABLES.items():
            merged = read_csv_frame(project.sent / "host" / "merged" / f"{table}.csv")
            expected = merged_columns(rules["primary_key"], column_rules(rules))
            assert list(merged.columns) == expected, (
                f"The guides name {table}'s columns as merge writes them"
            )
        assert len(scores) == 12 + 3 * 4, "One row per person"
        assert sorted(scores["person_id"], key=int) == [
            str(i) for i in range(1, len(scores) + 1)
        ], "New ids, 1 to M"

        roles = (*SITES, "linkage", "merge", "coordinator")
        for role in roles:
            project.finish(role)
        assert project.transfers == {
            *(
                (name, "coordinator", who)
                for who in (*SITES, "linkage", "merge")
                for name in (_bundle_of(who), f"{who}.guide.html")
            ),
            ("host.guide.html", "coordinator", "host"),
            *((f"{site}.public_key.pprl", site, "coordinator") for site in SITES),
            *((f"{site}.secret.pprl", "coordinator", site) for site in SITES),
            *((f"{site}.hashes.pprl", site, "linkage") for site in SITES),
            *((f"{site}.tables.pprl", site, "merge") for site in SITES),
            ("linkages.pprl", "linkage", "merge"),
            ("linkage_report.pprl", "linkage", "coordinator"),
            ("merged", "merge", "host"),
            *(
                (
                    next((tmp_path / role).glob(RECEIPT_GLOB)).name,
                    role,
                    "coordinator",
                )
                for role in roles
            ),
        }
        assert all(
            [f for f in p.rglob("*") if f.is_file()] == list(p.glob(RECEIPT_GLOB))
            for p in (tmp_path / role for role in roles)
        ), "Each folder is left with its receipt alone"
