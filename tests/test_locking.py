"""Test locking the secret to public keys, and how commands use the keys.

These tests cover what pprl init, secrets and hash do with the keys. The
secret never sits unencrypted in a file, the coordinator keeps no copy, and
a site's secret file unlocks only with that site's private key.
"""

import shutil
from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.hpke import AEAD, KDF, KEM, Suite

from pprl import _locking, _pprl_file, _workspace
from pprl import _messages as msg
from pprl._inputs import SECRET_LENGTH
from pprl._io import read_text, write_text
from pprl._pprl_file import PprlFileError
from pprl._shared import Member
from tests.commands import DESTROY_YES
from tests.helpers import (
    SITES,
    SYNTH_SECRET,
    coordinator_workspace,
    log_of,
    make_workspace,
    provenance,
    rewrite_header,
    run_in,
    shows,
    synthesize_patients,
    write_again,
)
from tests.test_usability import style_problems

if TYPE_CHECKING:
    from pathlib import Path

    from pprl._pprl_file import PprlFile
    from pprl._wording import Message

pytestmark = pytest.mark.security

# The id of a different project, or of a different bundle set.
OTHER_ID = "11111111-1111-4111-8111-111111111111"


def _key_pair(folder: Path, site: str = "site_a") -> tuple[PprlFile, PprlFile]:
    """Make a new key pair for ``site`` in ``folder``, as PprlFiles.

    Return (private, public).
    """
    private, public = (
        folder / f"{site}.private_key.pprl",
        folder / f"{site}.public_key.pprl",
    )
    _locking.write_key_pair(private, public, provenance(site))
    return _pprl_file.read(private), _pprl_file.read(public)


def _secret_file(
    folder: Path, public: PprlFile, secret: str = SYNTH_SECRET, site: str = "site_a"
) -> PprlFile:
    """Make a secret file for ``site`` in ``folder``, locked to ``public``.

    Return it as a PprlFile.
    """
    path = folder / f"{site}.secret.pprl"
    _pprl_file.write(
        path,
        "secret",
        {Member.KEY: _locking.lock(secret, public)},
        provenance=provenance(site),
    )
    return _pprl_file.read(path)


class TestLocking:
    """Locking the secret to a public key."""

    def test_unlocks(self, tmp_path: Path) -> None:
        """A secret locked to a public key unlocks with its private key."""
        private, public = _key_pair(tmp_path)
        locked = _secret_file(tmp_path, public)
        assert _locking.unlock(locked, private) == SYNTH_SECRET

    def test_never_in_the_clear(self, tmp_path: Path) -> None:
        """No key file holds the secret, and the locked secret differs each time.

        This way two sites' files reveal nothing about each other.
        """
        _, public = _key_pair(tmp_path)
        first, second = (_locking.lock(SYNTH_SECRET, public) for _ in range(2))
        assert SYNTH_SECRET not in str(first)
        assert SYNTH_SECRET.encode().hex() not in str(first)
        assert first["locked"] != second["locked"], "A new key each time"

    def test_private_key_only_in_its_file(self, tmp_path: Path) -> None:
        """The public key file holds no private key."""
        private, public = _key_pair(tmp_path)
        assert set(public.members[Member.KEY]) == {"public"}
        assert set(private.members[Member.KEY]) == {"private"}
        value = private.members[Member.KEY]["private"]
        assert value not in (tmp_path / "site_a.public_key.pprl").read_bytes().hex()

    def test_other_key(self, tmp_path: Path) -> None:
        """A different key pair's private key does not unlock the secret."""
        _, public = _key_pair(tmp_path)
        locked = _secret_file(tmp_path, public)
        other, _ = _key_pair(tmp_path, "site_b")
        with pytest.raises(PprlFileError) as problem:
            _locking.unlock(locked, other)
        assert shows(msg.SECRET_OTHER_KEY, str(problem.value))

    @pytest.mark.parametrize(
        "changes",
        [{"site": "site_b"}, {"project": OTHER_ID}, {"set": OTHER_ID}],
        ids=["site", "project", "set"],
    )
    def test_header_authenticated(self, tmp_path: Path, changes: dict) -> None:
        """A locked secret passed off as someone else's does not unlock.

        This covers passing it off as another site's, project's or bundle set's.
        """
        private, public = _key_pair(tmp_path)
        path = _secret_file(tmp_path, public).path
        rewrite_header(path, **changes)
        with pytest.raises(PprlFileError) as problem:
            _locking.unlock(_pprl_file.read(path), private)
        assert shows(msg.SECRET_OTHER_KEY, str(problem.value))

    @pytest.mark.parametrize(
        "position", [0, -1], ids=["the sender's key", "the encrypted secret"]
    )
    def test_changed(self, tmp_path: Path, position: int) -> None:
        """A locked secret with any part changed does not unlock."""
        private, public = _key_pair(tmp_path)
        secret_file = _secret_file(tmp_path, public)
        locked = bytearray.fromhex(secret_file.members[Member.KEY]["locked"])
        locked[position] ^= 1
        member = {"locked": locked.hex()}
        write_again(secret_file.path, {Member.KEY: member}, secret_file.header)
        with pytest.raises(PprlFileError) as problem:
            _locking.unlock(_pprl_file.read(secret_file.path), private)
        assert shows(msg.SECRET_OTHER_KEY, str(problem.value))

    def test_cut_short(self, tmp_path: Path) -> None:
        """A locked secret too short to hold a key does not unlock."""
        private, public = _key_pair(tmp_path)
        secret_file = _secret_file(tmp_path, public)
        secret_file.members[Member.KEY] = {"locked": "00"}
        with pytest.raises(PprlFileError) as problem:
            _locking.unlock(secret_file, private)
        assert shows(msg.SECRET_OTHER_KEY, str(problem.value))

    def test_standard_hpke(self, tmp_path: Path) -> None:
        """A locked secret is standard HPKE, which any implementation can open.

        It uses HPKE with X25519, HKDF-SHA256 and AES-256-GCM, given the file's
        ``info``.
        """
        private, public = _key_pair(tmp_path)
        secret_file = _secret_file(tmp_path, public)
        key = X25519PrivateKey.from_private_bytes(
            bytes.fromhex(private.members[Member.KEY]["private"])
        )
        opened = Suite(KEM.X25519, KDF.HKDF_SHA256, AEAD.AES_256_GCM).decrypt(
            bytes.fromhex(secret_file.members[Member.KEY]["locked"]),
            key,
            info=_locking._info(secret_file.header),
        )
        assert opened.decode("ascii") == SYNTH_SECRET

    @pytest.mark.parametrize(
        "value", ["not hexadecimal", "00", "00" * 32, None, 7], ids=str
    )
    def test_unusable_public_key(self, tmp_path: Path, value: bytes) -> None:
        """A public key file that holds no usable key counts as damaged.

        The key may be too short, not a key at all, or a point that every key
        exchange would agree on.
        """
        _, public = _key_pair(tmp_path)
        public.members[Member.KEY] = {"public": value}
        with pytest.raises(PprlFileError) as problem:
            _locking.lock(SYNTH_SECRET, public)
        assert shows(msg.FILE_DAMAGED, str(problem.value))

    @pytest.mark.parametrize("value", ["not hexadecimal", "00", None], ids=str)
    def test_unusable_private_key(self, tmp_path: Path, value: bytes) -> None:
        """A private key file that holds no usable key counts as damaged."""
        private, public = _key_pair(tmp_path)
        locked = _secret_file(tmp_path, public)
        private.members[Member.KEY] = {"private": value}
        with pytest.raises(PprlFileError) as problem:
            _locking.unlock(locked, private)
        assert shows(msg.FILE_DAMAGED, str(problem.value))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Start a project, and return the coordinator's folder.

    The project has the coordinator's workspace with its bundles, and each
    site's workspace, made with pprl init. Each site's public key is saved
    in the coordinator's workspace.
    """
    coordinator = tmp_path / "coordinator"
    coordinator_workspace(coordinator)
    for site in SITES:
        _site_workspace(tmp_path, site)
        shutil.copy(
            tmp_path / site / "outbox" / "coordinator" / f"{site}.public_key.pprl",
            coordinator,
        )
    return coordinator


def _site_workspace(root: Path, site: str, name: str | None = None) -> Path:
    """Make the workspace for ``site`` in a new folder under ``root``.

    Make it as pprl init does, from the bundle in the coordinator's outbox.
    """
    folder = root / (name or site)
    folder.mkdir()
    shutil.copy(root / "coordinator" / "outbox" / site / f"{site}.pprl", folder)
    rc, output = run_in(folder, ["init"])
    assert rc == 0, output
    return folder


def _received(coordinator: Path, site: str) -> Path:
    """Copy ``site``'s secret file from the coordinator's outbox into its workspace.

    Return the site's workspace.
    """
    folder = coordinator.parent / site
    shutil.copy(coordinator / "outbox" / site / f"{site}.secret.pprl", folder)
    return folder


def _unlocked(folder: Path, site: str) -> str:
    """Unlock the secret in ``site``'s workspace ``folder``."""
    return _locking.unlock(
        _pprl_file.read(folder / f"{site}.secret.pprl"),
        _pprl_file.read(folder / f"{site}.private_key.pprl"),
    )


def _ready_to_hash(folder: Path, site: str = "site_a") -> None:
    """Make ``site``'s workspace ``folder`` ready to hash, as if clean had run.

    The files belong to the bundle set named in the workspace's own marker.
    Tests of hash use this.
    """
    marker = _workspace.read(folder)
    assert (
        synthesize_patients(folder, n=5, site=site, output="clean.staged.pprl", seed=1)
        == 0
    )
    staged = folder / "clean.staged.pprl"
    rewrite_header(staged, project=marker.project, set=marker.bundle_set)
    staged.rename(folder / f"{site}.clean.pprl")


class TestInit:
    """The key pair pprl init makes for a site."""

    def test_key_pair(self, tmp_path: Path) -> None:
        """A site's pprl init keeps the private key and sends the public key.

        It keeps the private key in the workspace and puts the public key in
        the outbox. It neither shows nor logs either key.
        """
        coordinator = tmp_path / "coordinator"
        coordinator_workspace(coordinator)
        folder = _site_workspace(tmp_path, "site_a")
        private = _pprl_file.read(folder / "site_a.private_key.pprl", "private_key")
        public = _pprl_file.read(
            folder / "outbox" / "coordinator" / "site_a.public_key.pprl", "public_key"
        )
        marker = _workspace.read(folder)
        for pprl_file in (private, public):
            assert pprl_file.header["site"] == "site_a"
            assert pprl_file.header["project"] == marker.project
            assert pprl_file.header["set"] == marker.bundle_set
        assert [p.name for p in (folder / "outbox").iterdir()] == ["coordinator"]
        assert [p.name for p in (folder / "outbox" / "coordinator").iterdir()] == [
            "site_a.public_key.pprl"
        ]
        log = read_text(log_of(folder))
        assert private.members[Member.KEY]["private"] not in log
        assert public.members[Member.KEY]["public"] not in log

    def test_new_pair_each_workspace(self, tmp_path: Path) -> None:
        """Each workspace has its own key pair, even for the same site."""
        coordinator_workspace(tmp_path / "coordinator")
        first = _site_workspace(tmp_path, "site_a")
        second = _site_workspace(tmp_path, "site_a", "again")
        keys = [
            _pprl_file.read(
                f / "outbox" / "coordinator" / "site_a.public_key.pprl"
            ).members[Member.KEY]
            for f in (first, second)
        ]
        assert keys[0] != keys[1]

    def test_agents_have_no_keys(self, tmp_path: Path) -> None:
        """Only a site has a key pair."""
        coordinator_workspace(tmp_path / "coordinator")
        folder = tmp_path / "linkage"
        folder.mkdir()
        shutil.copy(tmp_path / "coordinator/outbox/linkage/linkage.pprl", folder)
        rc, output = run_in(folder, ["init"])
        assert rc == 0, output
        assert not (folder / "outbox").exists()
        assert [p.name for p in folder.glob("*.pprl")] == ["linkage.pprl"]

    def test_inspect_shows_no_key(self, tmp_path: Path) -> None:
        """Pprl inspect names what a key file holds, but never shows it."""
        coordinator_workspace(tmp_path / "coordinator")
        folder = _site_workspace(tmp_path, "site_a")
        private = _pprl_file.read(folder / "site_a.private_key.pprl")
        rc, output = run_in(folder, ["inspect", "site_a.private_key.pprl"])
        assert rc == 0, output
        assert msg.FILE_KINDS["private_key"] in output, output
        assert private.members[Member.KEY]["private"] not in output

    def test_key_there_already(self, tmp_path: Path) -> None:
        """Init stops in a folder that already holds a public key.

        It makes no workspace.
        """
        coordinator_workspace(tmp_path / "coordinator")
        folder = tmp_path / "site_a"
        (folder / "outbox").mkdir(parents=True)
        write_text(
            folder / "outbox" / "coordinator" / "site_a.public_key.pprl", "left here"
        )
        shutil.copy(tmp_path / "coordinator/outbox/site_a/site_a.pprl", folder)
        rc, output = run_in(folder, ["init"])
        assert rc == 1, output
        assert shows(msg.OUTPUT_EXISTS, output), output
        assert not _workspace.is_workspace(folder)
        assert not (folder / "site_a.private_key.pprl").exists()


def _secrets_refused(project: Path, says: Message | None = None) -> str:
    """Run pprl secrets in ``project``, and check that it is refused.

    The output shows ``says``, if given, and follows the rules for messages.
    No secret file is written. Return the output.
    """
    before = sorted((project / "outbox").glob("*/*.secret.pprl"))
    rc, output = run_in(project, ["secrets"])
    assert rc == 1, output
    if says is not None:
        assert shows(says, output), output
    assert not style_problems(output, rejected=True), output
    assert sorted((project / "outbox").glob("*/*.secret.pprl")) == before, (
        "A refused run writes no secret file"
    )
    return output


class TestSecrets:
    """The secret files pprl secrets writes."""

    def test_one_secret_for_every_site(self, project: Path) -> None:
        """Pprl secrets locks one new secret to each site's key.

        Each site unlocks the same secret, in pprl's own format. No other
        site's key opens a site's file.
        """
        rc, output = run_in(project, ["secrets"])
        assert rc == 0, output
        assert shows(msg.WROTE_SECRETS, output), output
        secrets = {_unlocked(_received(project, site), site) for site in SITES}
        assert len(secrets) == 1
        [secret] = secrets
        assert len(secret) == SECRET_LENGTH
        assert int(secret, 16) >= 0
        with pytest.raises(PprlFileError):
            _locking.unlock(
                _pprl_file.read(project.parent / "site_a" / "site_a.secret.pprl"),
                _pprl_file.read(project.parent / "site_b" / "site_b.private_key.pprl"),
            )

    def test_keeps_no_copy(self, project: Path) -> None:
        """The coordinator's workspace keeps no copy of the secret.

        The secret is in none of its files and no bundle. Nothing shown or
        logged contains it.
        """
        rc, output = run_in(project, ["secrets"])
        assert rc == 0, output
        secret = _unlocked(_received(project, "site_a"), "site_a")
        assert secret not in output
        for path in project.rglob("*"):
            if not path.is_file():
                continue
            assert secret.encode() not in path.read_bytes(), path
            if path.suffix == ".pprl":
                pprl_file = _pprl_file.read(path)
                assert secret not in str(pprl_file.header), path
                assert secret not in str(pprl_file.members), path
        for bundle in (project / "outbox").glob("site_?/site_?.pprl"):
            settings = _pprl_file.read(bundle).members["settings"]
            assert "secret" not in settings, bundle

    def test_never_replaces(self, project: Path) -> None:
        """An existing secret file stops pprl secrets.

        This prevents a site from being left with a secret that another site
        doesn't have. Once the file is deleted, a new secret is made.
        """
        rc, output = run_in(project, ["secrets"])
        assert rc == 0, output
        first = _unlocked(_received(project, "site_a"), "site_a")
        before = (project / "outbox" / "site_a" / "site_a.secret.pprl").read_bytes()
        _secrets_refused(project, msg.SECRET_EXISTS)
        assert (
            project / "outbox" / "site_a" / "site_a.secret.pprl"
        ).read_bytes() == before
        for path in (project / "outbox").glob("*/*.secret.pprl"):
            path.unlink()
        rc, output = run_in(project, ["secrets"])
        assert rc == 0, output
        (project.parent / "site_a" / "site_a.secret.pprl").unlink()
        assert _unlocked(_received(project, "site_a"), "site_a") != first

    def test_needs_every_key(self, project: Path) -> None:
        """A missing public key stops it, names the file, and writes nothing."""
        (project / "site_b.public_key.pprl").unlink()
        output = _secrets_refused(project, msg.FILE_NOT_FOUND)
        assert "site_b.public_key.pprl" in output

    def test_other_project(self, project: Path) -> None:
        """A public key from another project is refused."""
        rewrite_header(project / "site_b.public_key.pprl", project=OTHER_ID)
        _secrets_refused(project, msg.FILE_OTHER_PROJECT)

    def test_other_site(self, project: Path) -> None:
        """One site's public key under another site's name is refused."""
        path = project / "site_b.public_key.pprl"
        path.unlink()
        shutil.copy(project / "site_a.public_key.pprl", path)
        _secrets_refused(project, msg.FILE_OF_OTHER_SITE)

    def test_private_key_is_not_a_public_key(self, project: Path) -> None:
        """A private key sent by mistake is refused as a private key, not used."""
        path = project / "site_b.public_key.pprl"
        path.unlink()
        shutil.copy(project.parent / "site_b" / "site_b.private_key.pprl", path)
        output = _secrets_refused(project, msg.FILE_OTHER_KIND)
        assert msg.FILE_KINDS["private_key"] in output, output

    def test_several_bundle_sets(self, project: Path) -> None:
        """Keys from different bundle sets are refused."""
        rewrite_header(project / "site_b.public_key.pprl", set=OTHER_ID)
        _secrets_refused(project, msg.KEYS_OF_SEVERAL_SETS)

    def test_unusable_key(self, project: Path) -> None:
        """A public key file that holds no usable key is refused as damaged."""
        path = project / "site_b.public_key.pprl"
        key = _pprl_file.read(path)
        write_again(path, {Member.KEY: {"public": "00"}}, key.header)
        _secrets_refused(project, msg.FILE_DAMAGED)

    def test_no_project_settings(self, tmp_path: Path) -> None:
        """Without project.yml it stops, because it can't know the sites."""
        make_workspace(tmp_path, role="coordinator")
        _secrets_refused(tmp_path)


class TestHash:
    """Pprl hash, with the secret it unlocks."""

    def test_hashes_with_the_secret(self, project: Path) -> None:
        """Pprl hash unlocks the secret and hashes with it.

        Two sites' hashes of one person agree, because they share one secret.
        """
        rc, output = run_in(project, ["secrets"])
        assert rc == 0, output
        hashes = []
        for site in SITES:
            folder = _received(project, site)
            _ready_to_hash(folder, site)
            rc, output = run_in(folder, ["hash"])
            assert rc == 0, output
            assert shows(msg.WROTE_HASHES, output), output
            path = folder / "outbox" / "linkage" / f"{site}.hashes.pprl"
            hashes.append(_pprl_file.read(path).members[Member.HASHES])
            secret = _unlocked(folder, site)
            assert secret not in output
            assert secret not in read_text(log_of(folder))
        assert hashes[0].equals(hashes[1]), "The same seed: the same people"

    def test_no_secret_file(self, project: Path) -> None:
        """Hash stops without the secret file and writes nothing."""
        folder = project.parent / "site_a"
        _ready_to_hash(folder)
        rc, output = run_in(folder, ["hash"])
        assert rc == 1, output
        assert shows(msg.FILE_NOT_FOUND, output), output
        assert "site_a.secret.pprl" in output, output
        assert not (folder / "outbox" / "linkage" / "site_a.hashes.pprl").exists()

    def test_wrong_sites_file(self, project: Path) -> None:
        """Another site's secret file, under this site's name, is refused."""
        rc, output = run_in(project, ["secrets"])
        assert rc == 0, output
        folder = project.parent / "site_a"
        _ready_to_hash(folder)
        shutil.copy(
            project / "outbox" / "site_b" / "site_b.secret.pprl",
            folder / "site_a.secret.pprl",
        )
        rc, output = run_in(folder, ["hash"])
        assert rc == 1, output
        assert shows(msg.FILE_OF_OTHER_SITE, output), output
        assert not (folder / "outbox" / "linkage" / "site_a.hashes.pprl").exists()

    def test_damaged_file(self, project: Path) -> None:
        """A secret file cut short is refused as damaged."""
        rc, output = run_in(project, ["secrets"])
        assert rc == 0, output
        folder = _received(project, "site_a")
        _ready_to_hash(folder)
        path = folder / "site_a.secret.pprl"
        path.write_bytes(path.read_bytes()[:-8])
        rc, output = run_in(folder, ["hash"])
        assert rc == 1, output
        assert shows(msg.FILE_DAMAGED, output), output
        assert not (folder / "outbox" / "linkage" / "site_a.hashes.pprl").exists()

    def test_lost_private_key(self, project: Path) -> None:
        """Without the private key, hash stops and names the missing file."""
        rc, output = run_in(project, ["secrets"])
        assert rc == 0, output
        folder = _received(project, "site_a")
        _ready_to_hash(folder)
        (folder / "site_a.private_key.pprl").unlink()
        rc, output = run_in(folder, ["hash"])
        assert rc == 1, output
        assert shows(msg.FILE_NOT_FOUND, output), output
        assert "site_a.private_key.pprl" in output, output

    def test_destroyed_workspace(self, project: Path) -> None:
        """Destroying a workspace deletes its private key.

        The site's secret file, if kept elsewhere, does not unlock in a new
        workspace. pprl says what to do, and hashes nothing.
        """
        rc, output = run_in(project, ["secrets"])
        assert rc == 0, output
        folder = project.parent / "site_a"
        rc, output = run_in(folder, ["destroy"], DESTROY_YES)
        assert rc == 0, output
        assert not (folder / "site_a.private_key.pprl").exists()
        rc, output = run_in(folder, ["hash"])
        assert rc == 1, output
        new = _site_workspace(project.parent, "site_a", "new")
        _ready_to_hash(new)
        shutil.copy(project / "outbox" / "site_a" / "site_a.secret.pprl", new)
        rc, output = run_in(new, ["hash"])
        assert rc == 1, output
        assert shows(msg.SECRET_OTHER_KEY, output), output
        assert not (new / "outbox" / "linkage" / "site_a.hashes.pprl").exists()
