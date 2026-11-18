"""Pin the exact version of every package pprl runs with, in the wheel's requirements.

    uv run --frozen python -m tests.pins             (make pins)
    uv run --frozen python -m tests.pins --upgrade   (make pins-upgrade)

pprl's own requirements are written loosely, by hand, under [tool.pprl]
requires in pyproject.toml. This tool resolves them. It keeps uv.lock's
versions, or, with --upgrade, takes the newest versions they allow. It then
writes every package they bring in, directly or indirectly, with its exact
version, into [project] dependencies, which the wheel declares. This way
`uv tool install` of the wheel installs the same versions at every site, on
any day, as the tests ran with. Each package installed straight from a URL
(the forks of anonlink and clkhash) also carries its wheel's SHA-256 from
uv.lock, as ``#sha256=...``, so uv installs only those exact bytes. Finally
it runs `uv lock`. A maintainability test checks that the pins and uv.lock
agree.
"""

import re
import sys
import tomllib
from pathlib import Path

from pprl._io import read_text, write_text
from tests.commands import run_text

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "uv.lock"
# Matches a requirement installed from a URL: its name, its URL, and the rest.
_DIRECT = re.compile(r"^(?P<name>\S+) @ (?P<url>[^\s#;]+)(?:#\S*)?(?P<rest>.*)$")
# Matches the [project] dependencies list, which this tool rewrites.
_PINS = re.compile(r"^dependencies = \[\n.*?^\]\n", re.MULTILINE | re.DOTALL)
_HEADER = (
    "# Every package pprl runs with, at its exact version: written by\n"
    "# `make pins` (python -m tests.pins) from [tool.pprl] requires; do not edit\n"
)


def _uv(*args: str, cwd: Path) -> str:
    return run_text(["uv", *args], cwd=cwd, check=True, capture_output=True).stdout


def resolved(*, upgrade: bool = False) -> list:
    """List every package that [tool.pprl] requires brings in, as uv resolves it.

    Each line looks like ``numpy==2.5.0``, with any environment marker. To
    do this, the project is locked with its loose requirements in place of
    the pins, so uv keeps uv.lock's versions. Then pyproject.toml is put back.
    """
    text = read_text(PYPROJECT)
    loose = "".join(f"  {requirement!r},\n" for requirement in requirements())
    try:
        write_text(PYPROJECT, _write(text, loose), replace=True)
        _uv("lock", *(["--upgrade"] if upgrade else []), cwd=ROOT)
        pins = locked()
    finally:
        write_text(PYPROJECT, text, replace=True)
    return pins


def locked() -> list:
    """List every package uv.lock installs to run pprl, without the dev tools.

    Each line looks like ``numpy==2.5.0``, with any environment marker. A
    package installed from a URL ends its URL with ``#sha256=`` and the
    wheel's hash in uv.lock.
    """
    exported = _uv(
        "export",
        "--frozen",
        "--no-dev",
        "--no-hashes",
        "--no-emit-project",
        "--no-header",
        "--no-annotate",
        cwd=ROOT,
    )
    hashes = _wheel_hashes()
    return [
        _with_hash(line, hashes)
        for line in exported.splitlines()
        if line and not line.startswith("#")
    ]


def _wheel_hashes() -> dict:
    """Map each URL uv.lock installs a package from to its wheel's ``sha256:...``."""
    hashes = {}
    for package in tomllib.loads(read_text(LOCK)).get("package", []):
        url = package.get("source", {}).get("url")
        if url is None:
            continue
        for wheel in package.get("wheels", []):
            if wheel.get("url", "").split("#")[0] == url.split("#")[0]:
                hashes[url.split("#")[0]] = wheel["hash"]
    return hashes


def _with_hash(line: str, hashes: dict) -> str:
    """Return a requirement line, with its wheel's hash if it names a URL."""
    match = _DIRECT.match(line)
    if match is None:
        return line
    url = match["url"]
    algorithm, _, digest = hashes.get(url, "").partition(":")
    if algorithm != "sha256" or not digest:
        reason = f"uv.lock has no SHA-256 for {url}"
        raise ValueError(reason)
    return f"{match['name']} @ {url}#sha256={digest}{match['rest']}"


def _write(text: str, listed: str) -> str:
    """Return pyproject.toml's text, with ``listed`` as [project] dependencies."""
    text = _PINS.sub(lambda _: f"{_HEADER}dependencies = [\n{listed}]\n", text, count=1)
    return text.replace(_HEADER + _HEADER, _HEADER)


def requirements() -> list:
    """Return pprl's own loose requirements, from [tool.pprl] requires."""
    return tomllib.loads(read_text(PYPROJECT))["tool"]["pprl"]["requires"]


def pinned() -> list:
    """List the pins that [project] dependencies currently holds."""
    return tomllib.loads(read_text(PYPROJECT))["project"]["dependencies"]


def main(argv: list[str]) -> int:
    """Record exact package versions in pyproject.toml and uv.lock; return exit code."""
    pins = resolved(upgrade="--upgrade" in argv)
    listed = "".join(f'  "{pin}",\n' for pin in pins)
    text = read_text(PYPROJECT)
    if not _PINS.search(text):
        print("pyproject.toml has no [project] dependencies list to write")
        return 1
    write_text(PYPROJECT, _write(text, listed), replace=True)
    _uv("lock", cwd=ROOT)
    print(f"Pinned {len(pins)} packages in pyproject.toml, and locked them.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
