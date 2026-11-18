"""Install a wheel as a user would, and create every role's workspace with it.

    uv run python -m tests.wheel_check dist/pprl-3.0.0-py3-none-any.whl

CI runs this on Linux, macOS (Apple silicon or Intel) and Windows before a
wheel is attached to a release. In a temporary folder, it installs the
wheel with ``uv tool install``, using uv tool folders of its own. Then, using
only the installed pprl, it:

1. creates the coordinator's workspace from the example project.yml, and
   makes synthetic data, every bundle and every guide
2. runs pprl init on every bundle, each in a folder of its own
3. runs pprl destroy in every workspace, answering yes

It exits with code 1 at the first step that fails, after showing that
step's output.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from pprl import _pprl_file
from pprl._io import read_text, write_text
from pprl._shared import OUTBOX
from pprl._workspace import MARKER_NAME
from tests.commands import DESTROY_YES, run_text

if TYPE_CHECKING:
    from typing import Never

SOURCE_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = SOURCE_ROOT / "examples"
# A small scenario using the example project's sites, for pprl synth.
SCENARIO = {
    "seed": "wheel check",
    "sites": ["site_a", "site_b"],
    "outputs": ["patients"],
    "generated_individuals": [{"sites": ["site_a", "site_b"], "count": 5}],
}
ROLES = ("site", "linkage", "merge")


def _run(
    step: str,
    command: list[str | Path],
    folder: Path,
    answers: str = "",
    env: dict[str, str] | None = None,
) -> str:
    """Run ``command`` in ``folder``, typing ``answers``, and return its output.

    If the command fails, stop the check and show the output.
    """
    print(f"{step} ... ", end="", flush=True)
    result = run_text(
        [str(part) for part in command],
        cwd=folder,
        input=answers,
        capture_output=True,
        errors="replace",
        env=env,
    )
    if result.returncode != 0:
        print(f"failed (exit code {result.returncode})")
        print(result.stdout, result.stderr, sep="\n")
        sys.exit(1)
    print("ok")
    return result.stdout


def _fail(problem: str) -> Never:
    print(f"failed: {problem}")
    sys.exit(1)


def _installed(wheel: Path, tools: Path) -> tuple[dict[str, str], str]:
    """Install ``wheel`` with uv tool install, in folders under ``tools``.

    Return the environment to run the installed pprl in, and that pprl.
    """
    env = {
        k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")
    }
    env |= {"UV_TOOL_DIR": str(tools / "tools"), "UV_TOOL_BIN_DIR": str(tools / "bin")}
    _run("uv tool install", ["uv", "tool", "install", wheel.resolve()], tools, env=env)
    pprl = shutil.which("pprl", path=str(tools / "bin"))
    if pprl is None:
        _fail(f"uv tool install put no pprl in {tools / 'bin'}")
    # The installed pprl must run its own copy of pprl, never this clone's.
    environment = tools / "tools" / "pprl"
    python = shutil.which(
        "python", path=os.pathsep.join(str(environment / d) for d in ("bin", "Scripts"))
    )
    if python is None:
        _fail(f"uv tool install made no Python in {environment}")
    where = _run(
        "pprl from the wheel",
        [python, "-c", "import pprl; print(pprl.__file__)"],
        tools,
        env=env,
    )
    if not Path(where.strip()).resolve().is_relative_to((tools / "tools").resolve()):
        _fail(f"the installed pprl imports pprl from {where.strip()}")
    return env, pprl


def _coordinator(
    root: Path, pprl: str, env: dict[str, str]
) -> tuple[Path, Path, list[Path]]:
    """Make the coordinator's workspace in ``root``/project with the installed ``pprl``.

    Make its test data, bundles and guides. Return the project's folder, the
    coordinator's folder, and the bundles.
    """
    project = root / "project"
    project.mkdir()
    coordinator = project / "coordinator"
    coordinator.mkdir()
    shutil.copy(EXAMPLES / "project.yml", coordinator)
    shutil.copy(EXAMPLES / "deployment.yml", coordinator)
    write_text(coordinator / "synth.yml", yaml.safe_dump(SCENARIO))
    for command in ("init", "synth", "bundles", "guides"):
        _run(f"pprl {command}", [pprl, command], coordinator, env=env)
    if not (coordinator / "synth" / "site_a" / "patients.csv").is_file():
        _fail("pprl synth wrote no patient file for site_a")
    if not (coordinator / "coordinator.guide.html").is_file():
        _fail("pprl guides wrote no guide for the coordinator")
    bundles = sorted((coordinator / OUTBOX).glob("*/*.pprl"))
    roles = {_pprl_file.read_header(b)["role"] for b in bundles}
    if roles != set(ROLES):
        _fail(f"the bundles are for {sorted(roles)}, not every role")
    return project, coordinator, bundles


def _every_role(
    project: Path,
    coordinator: Path,
    bundles: list[Path],
    *,
    wheel: Path,
    pprl: str,
    env: dict[str, str],
) -> None:
    """Make each role's workspace, then destroy them all.

    Each workspace is made from its bundle, and the secrets are locked and
    read. Then every workspace is destroyed, leaving its receipt.
    """
    release = wheel.name.split("-")[1]
    names = [bundle.stem for bundle in bundles]
    for bundle, name in zip(bundles, names, strict=True):
        folder = project / name
        folder.mkdir()
        shutil.copy(bundle, folder / bundle.name)
        _run(f"pprl init in {name}", [pprl, "init"], folder, env=env)
        marker = yaml.safe_load(read_text(folder / MARKER_NAME))
        if marker["release"] != release:
            _fail(f"{name}'s marker records {marker['release']}")
    sites = [name for name in names if name not in ("linkage", "merge")]
    for site in sites:
        shutil.copy(
            project / site / OUTBOX / "coordinator" / f"{site}.public_key.pprl",
            coordinator,
        )
    _run("pprl secrets", [pprl, "secrets"], coordinator, env=env)
    for site in sites:
        secret = f"{site}.secret.pprl"
        shutil.copy(coordinator / OUTBOX / site / secret, project / site)
        _run(
            f"pprl inspect in {site}",
            [pprl, "inspect", secret],
            project / site,
            env=env,
        )
    for name in (*names, "coordinator"):
        folder = project / name
        _run(
            f"pprl destroy in {name}",
            [pprl, "destroy"],
            folder,
            DESTROY_YES,
            env,
        )
        left = [p for p in folder.rglob("*") if p.is_file()]
        if len(left) != 1 or not left[0].name.startswith(f"{name}.receipt_"):
            _fail(f"{name} was not destroyed, with a receipt")


def main(argv: list[str]) -> int:
    """Install the wheel and create every role's workspace; return the exit code."""
    if len(argv) != 1 or not Path(argv[0]).is_file():
        print(__doc__)
        return 2
    wheel = Path(argv[0])
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        env, pprl = _installed(wheel, root)
        project, coordinator, bundles = _coordinator(root, pprl, env)
        _every_role(project, coordinator, bundles, wheel=wheel, pprl=pprl, env=env)
    print(f"{wheel.name} installs, and works for every role.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
