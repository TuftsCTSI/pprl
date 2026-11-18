"""Run pprl the way a user does, with the commands' text and what a user types.

Nothing here may need pytest or any other development package, because the
wheel check uses this module too, and CI runs that check without them. The
helpers module offers the same names to the tests.
"""

import subprocess
from typing import TYPE_CHECKING

from pprl import _messages as msg

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

# What a user types to confirm pprl destroy.
DESTROY_YES = f"{msg.DESTROY_YES}\n"


def run_text(
    command: Sequence[str | Path],
    *,
    check: bool = False,
    cwd: Path | str | None = None,
    capture_output: bool = False,
    input: str | None = None,  # noqa: A002 (subprocess.run's name)
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    errors: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run subprocess.run, with the command's input and output as UTF-8.

    This matches how pprl writes text, whatever the computer's own encoding.
    """
    return subprocess.run(  # noqa: S603 (the tests' own commands)
        command,
        check=check,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        capture_output=capture_output,
        input=input,
        env=env,
        timeout=timeout,
        errors=errors,
    )
