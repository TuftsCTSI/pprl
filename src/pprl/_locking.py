"""Lock the project's secret to each site's public key, and unlock it.

``pprl init`` makes a key pair for a site. ``pprl secrets`` holds no key: it
locks the secret to each site's public key and keeps no copy. ``pprl hash``
then unlocks the secret with the site's private key.

Locking uses HPKE (RFC 9180) in its base mode, with X25519, HKDF-SHA256 and
AES-256-GCM. The project, bundle set and site from the file's header are
included in the HPKE ``info``. This binds the locked secret to them, so it
cannot be passed off as the secret of another project or site.

Each of these files is a pprl file with one JSON member, ``key``, which holds
bytes written in hexadecimal:

    private_key   site_a.private_key.pprl   {"private": "..."}
    public_key    site_a.public_key.pprl    {"public": "..."}
    secret        site_a.secret.pprl        {"locked": "..."}
"""

import json
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.hpke import AEAD, KDF, KEM, Suite
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import _messages as msg
from . import _pprl_file
from ._io import display_name, for_writing_together
from ._pprl_file import PprlFileError
from ._shared import Member

if TYPE_CHECKING:
    from pathlib import Path

    from ._pprl_file import PprlFile

_SUITE = Suite(KEM.X25519, KDF.HKDF_SHA256, AEAD.AES_256_GCM)
_INFO = b"pprl secret 1 "


def write_key_pair(
    private_path: Path | str, public_path: Path | str, provenance: dict
) -> None:
    """Write a new key pair. Either both files are written, or neither is.

    The private key goes to ``private_path`` and the public key to
    ``public_path``. ``provenance`` gives the project, bundle set and site,
    under the keys project, set and site.
    """
    private_key = X25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    with for_writing_together(private_path, public_path):
        _pprl_file.write(
            private_path,
            "private_key",
            {Member.KEY: {"private": private_key.private_bytes_raw().hex()}},
            provenance=provenance,
        )
        _pprl_file.write(
            public_path,
            "public_key",
            {Member.KEY: {"public": public_bytes.hex()}},
            provenance=provenance,
        )


def _value(pprl_file: PprlFile, name: str) -> bytes:
    """Return the bytes stored under ``name`` in the ``key`` member of ``pprl_file``.

    Raise PprlFileError if there are none.
    """
    try:
        return bytes.fromhex(pprl_file.members[Member.KEY][name])
    except KeyError, TypeError, ValueError, AttributeError:
        raise _damaged(pprl_file) from None


def _damaged(pprl_file: PprlFile) -> PprlFileError:
    return PprlFileError(msg.FILE_DAMAGED(name=display_name(pprl_file.path)))


def _info(header: dict) -> bytes:
    """Return the HPKE ``info`` bytes that a locked secret is bound to.

    They hold pprl's own label and the project, bundle set and site from the
    file's header.
    """
    fields = {key: header[key] for key in ("project", "set", "site")}
    return _INFO + json.dumps(fields, sort_keys=True).encode("utf-8")


def lock(secret: str, key_file: PprlFile) -> dict:
    """Lock the text ``secret`` to the public key in ``key_file``.

    Return the ``key`` member of a secret file. Raise PprlFileError if
    ``key_file`` does not hold a usable public key.
    """
    try:
        public_key = X25519PublicKey.from_public_bytes(_value(key_file, "public"))
        locked = _SUITE.encrypt(
            secret.encode("ascii"), public_key, info=_info(key_file.header)
        )
    except ValueError:
        raise _damaged(key_file) from None
    return {"locked": locked.hex()}


def unlock(secret_file: PprlFile, private_file: PprlFile) -> str:
    """Unlock the secret in ``secret_file`` with the private key in ``private_file``.

    Raise PprlFileError if either file is unusable, or if the secret was
    locked to a different key.
    """
    try:
        private_key = X25519PrivateKey.from_private_bytes(
            _value(private_file, "private")
        )
    except ValueError:
        raise _damaged(private_file) from None
    locked = _value(secret_file, "locked")
    try:
        secret = _SUITE.decrypt(locked, private_key, info=_info(secret_file.header))
        return secret.decode("ascii")
    except InvalidTag, ValueError:
        raise PprlFileError(
            msg.SECRET_OTHER_KEY(
                name=display_name(secret_file.path),
                site=secret_file.header["site"],
            )
        ) from None
