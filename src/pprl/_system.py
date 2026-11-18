"""Gather the facts that the log's [INFO] lines report.

The facts cover how pprl was installed, the versions of the packages that
affect what it computes, the computer's processors and memory, the run's
peak memory, and where a crash happened.

Every function returns facts only. None returns text from a data file or a
path on the computer. The only names they return are the file names that
pprl publishes (its wheels), host names, and places in code. A place in code
is named relative to its package.
"""

import json
import os
import sys
import traceback
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

# The packages whose versions affect what pprl computes or how it reads files.
PACKAGES = ("anonlink", "bitarray", "clkhash", "numpy", "pandas", "PyYAML")
# The address where pprl's release wheels are published.
RELEASES = "https://github.com/TuftsCTSI/pprl/releases/download/"


def packages() -> dict:
    """Return the version of each package in PACKAGES, or None if not installed.

    >>> sorted(packages()) == sorted(PACKAGES)
    True
    """
    found = {}
    for name in PACKAGES:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            found[name] = None
    return found


class _Metadata(Protocol):
    """The part of a distribution that ``install_method`` reads: its metadata."""

    def read_text(self, filename: str, /) -> str | None: ...


def install_method(prefix: Path | None = None, dist: _Metadata | None = None) -> dict:
    """Find out how pprl was installed.

    Return a dict with these keys:

    - ``kind``: uv tool, uv, pip, other or unknown. For other, ``installer``
      names the installer.
    - ``source``: release wheel, remote wheel, local wheel, editable, source
      or index. ``wheel`` and ``host`` are added where they apply.

    ``uv tool install`` leaves uv-receipt.toml in the tool's environment. Any
    installer records its own name in INSTALLER. It records where it
    installed from in direct_url.json (PEP 610): a URL, a local file, or a
    source folder.
    """
    prefix = Path(sys.prefix if prefix is None else prefix)
    dist = distribution("pprl") if dist is None else dist
    installer = (dist.read_text("INSTALLER") or "").strip()
    if (prefix / "uv-receipt.toml").is_file():
        kind = "uv tool"
    elif installer in ("uv", "pip"):
        kind = installer
    else:
        kind = "other" if installer else "unknown"
    method = {"kind": kind, "installer": installer}
    try:
        direct = json.loads(dist.read_text("direct_url.json") or "null")
    except ValueError:
        direct = None
    if not isinstance(direct, dict):
        return method | {"source": "index"}
    url = urlsplit(str(direct.get("url", "")))
    if "dir_info" in direct:
        editable = bool(direct["dir_info"].get("editable"))
        return method | {"source": "editable" if editable else "source"}
    wheel = Path(url.path).name
    if url.scheme == "file":
        return method | {"source": "local wheel", "wheel": wheel}
    if direct["url"].startswith(RELEASES):
        return method | {"source": "release wheel", "wheel": wheel}
    return method | {"source": "remote wheel", "wheel": wheel, "host": url.hostname}


def cpus() -> int:
    """Return the number of processors, which is the default number of workers."""
    return os.cpu_count() or 1


def total_memory() -> int | None:
    """Return the computer's total memory in bytes, or None if it is unknown."""
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415 (Windows only)

        class Status(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                ("total", ctypes.c_ulonglong),
                ("available", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong),
                ("available_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended", ctypes.c_ulonglong),
            ]

        status = Status(length=ctypes.sizeof(Status))
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total
        return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except AttributeError, OSError, ValueError:
        return None


def peak_memory() -> int | None:
    """Return the most memory this run has used at one time, in bytes.

    The figure covers pprl's own process and its largest worker process.
    Return None if it cannot be found.
    """
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415 (Windows only)
        from ctypes import wintypes  # noqa: PLC0415 (Windows only)

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_faults", wintypes.DWORD),
                ("peak_working_set", ctypes.c_size_t),
                ("working_set", ctypes.c_size_t),
                ("quota_peak_paged", ctypes.c_size_t),
                ("quota_paged", ctypes.c_size_t),
                ("quota_peak_non_paged", ctypes.c_size_t),
                ("quota_non_paged", ctypes.c_size_t),
                ("pagefile", ctypes.c_size_t),
                ("peak_pagefile", ctypes.c_size_t),
            ]

        counters = Counters(cb=ctypes.sizeof(Counters))
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            process, ctypes.byref(counters), counters.cb
        ):
            return counters.peak_working_set
        return None
    try:
        import resource  # noqa: PLC0415 (not on Windows)
    except ImportError:
        return None
    # ru_maxrss is in kilobytes on Linux but in bytes on macOS.
    unit = 1 if sys.platform == "darwin" else 1024
    own = _own_peak() or resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
    workers = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * unit
    return max(own, workers)


def _own_peak() -> int | None:
    """Return this process's peak memory in bytes, read from /proc on Linux.

    This uses VmHWM, which starts from zero when pprl starts. getrusage's
    peak would include the process that started pprl. Return None on other
    systems.
    """
    try:
        with Path("/proc/self/status").open("rb") as status:
            for line in status:
                if line.startswith(b"VmHWM:"):
                    return int(line.split()[1]) * 1024  # The value is in kB.
    except OSError, ValueError, IndexError:
        pass
    return None


def _place(filename: str) -> str:
    """Name a code file relative to its package, leaving out install folders.

    For example, a pandas file is named from its pandas folder downward.
    """
    parts = Path(filename).parts
    for marker in ("site-packages", "dist-packages", "src"):
        if marker in parts:
            return "/".join(parts[len(parts) - parts[::-1].index(marker) :])
    if "pprl" in parts:
        return "/".join(parts[len(parts) - 1 - parts[::-1].index("pprl") :])
    return Path(filename).name


def crash_frames(error: BaseException) -> list:
    """Find where ``error`` happened, as a list of (file, line, function).

    The list runs from the outermost call to the innermost. It includes every
    place in pprl's code, and the innermost place outside it (in a library)
    where the error was raised. It never includes the error's text or a
    variable's value.

    >>> try:
    ...     {}["key"]
    ... except KeyError as e:
    ...     crash_frames(e)[-1][2]
    '<module>'
    """
    frames = traceback.extract_tb(error.__traceback__)
    places = [(_place(f.filename), f.lineno, f.name) for f in frames]
    ours = [p for p in places if p[0].startswith("pprl/")]
    if places and places[-1] not in ours:
        ours.append(places[-1])
    return ours


def causes(error: BaseException) -> list:
    """List the types of the errors being handled when ``error`` was raised.

    The innermost comes first. Only the types are given.
    """
    kinds = []
    seen = {id(error)}
    current: BaseException | None = error
    while (current := current.__cause__ or current.__context__) and id(
        current
    ) not in seen:
        seen.add(id(current))
        kinds.append(type(current).__name__)
    return kinds
