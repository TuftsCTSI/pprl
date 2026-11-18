"""Stop pprl on a computer that its linkage library has no build for.

anonlink, which link uses, is compiled, and pprl's fork of it is built only
for the platforms in SUPPORTED. pyproject.toml installs it only on those, so
pprl itself still installs elsewhere. Without this check, pprl would then stop
with a traceback as soon as it imported anonlink. Instead, this module is
imported before anonlink, and stops pprl with a plain message.

SUPPORTED must agree with the environment markers on anonlink in
[tool.pprl] requires. Python reports the machine as platform_machine does.
"""

import platform
import sys

# (sys.platform, platform.machine()) for every platform with an anonlink wheel.
SUPPORTED = frozenset(
    {
        ("linux", "x86_64"),
        ("darwin", "x86_64"),
        ("darwin", "arm64"),
        ("win32", "AMD64"),
    }
)


def is_supported(system: str = sys.platform, machine: str | None = None) -> bool:
    """Tell whether anonlink has a wheel for this system and machine.

    >>> is_supported("linux", "x86_64")
    True
    >>> is_supported("linux", "aarch64")
    False
    """
    machine = platform.machine() if machine is None else machine
    return (system, machine) in SUPPORTED


def stop_if_unsupported() -> None:
    """Stop pprl with exit code 1 if this computer is not supported."""
    if is_supported():
        return
    from . import _messages as msg  # noqa: PLC0415 (only needed to stop)

    message = msg.UNSUPPORTED_PLATFORM(
        system=platform.system() or sys.platform,
        machine=platform.machine() or "an unknown processor",
    )
    first, *rest = message.splitlines()
    sys.stderr.write(f"[STOP] {first}\n" + "".join(f"       {r}\n" for r in rest))
    sys.stderr.flush()
    raise SystemExit(1)
