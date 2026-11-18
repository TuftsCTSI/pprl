"""Report the benchmarks' results as a table, with an estimate for larger sites.

    uv run python -m tests.benchmarks.report build/benchmarks/latest.json

Each step's time at the largest size measured is scaled to ESTIMATE_AT
records per site. Link's time scales with the square of the size, because
it compares every record of one site with every record of the other. The
other steps scale in proportion. Peak memory is scaled by how it grew
between the two largest sizes measured, above the memory every run needs.
The exception is a step that works in fixed-size buckets (BOUNDED), whose
largest measured peak is used as is. These are estimates, which only a run
at that size can confirm.
"""

import json
import sys
from typing import TYPE_CHECKING

from pprl._io import read_text

if TYPE_CHECKING:
    from collections.abc import Iterator

ESTIMATE_AT = 1_500_000
# How a step's time grows with the number of records per site (the exponent).
GROWTH = {"link": 2}
# The steps whose memory does not grow with the number of records per site.
BOUNDED = {"select", "pack", "merge"}
MEMORY_TARGET = "4 GB"
_UNITS = {"bytes": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}


def rows(results: dict) -> Iterator[tuple]:
    """Yield (step, size, seconds, memory) for each benchmark, in the order run."""
    for bench in results["benchmarks"]:
        info = bench["extra_info"]
        yield info["step"], info["size"], bench["stats"]["mean"], info["memory"]


def _bytes(memory: str | None) -> float | None:
    """Read a size as pprl logs it (such as "905.0 MB") in bytes, or None if unknown."""
    if memory is None:
        return None
    number, _, unit = memory.partition(" ")
    try:
        return float(number) * _UNITS[unit]
    except ValueError, KeyError:
        return None


def _size(count: float | None) -> str:
    if count is None:
        return "unknown"
    return (
        f"{count / 1024**3:.1f} GB" if count >= 1024**3 else f"{count / 1024**2:.0f} MB"
    )


def _time(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.1f} s"
    if seconds < 7200:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def _memory(step: str, measured: list[tuple[int, float]]) -> tuple[float | None, str]:
    """Estimate a step's peak memory at ESTIMATE_AT, and describe how.

    ``measured`` gives the (size, bytes) measurements.
    """
    measured = sorted(measured)
    if not measured:
        return None, "memory unknown"
    if step in BOUNDED:
        return max(b for _, b in measured), "memory bounded"
    if len(measured) == 1:
        size, peak = measured[0]
        return peak * ESTIMATE_AT / size, "memory in proportion"
    (small, low), (large, high) = measured[-2:]
    per_record = max(high - low, 0) / (large - small)
    return high + per_record * (ESTIMATE_AT - large), "memory by its growth"


def table(results: dict) -> str:
    """Tabulate the results: a row per step and size, then each estimate."""
    lines = [
        "| Step | Records per site | Time | Peak memory |",
        "| --- | ---: | ---: | ---: |",
    ]
    largest, memories = {}, {}
    for step, size, seconds, memory in rows(results):
        lines.append(f"| {step} | {size:,} | {_time(seconds)} | {memory} |")
        if size >= largest.get(step, (0, 0))[0]:
            largest[step] = (size, seconds)
        if _bytes(memory):
            memories.setdefault(step, []).append((size, _bytes(memory)))
    lines += ["", f"Estimated at {ESTIMATE_AT:,} records per site:", ""]
    lines += [
        f"| Step | Time | Peak memory (target {MEMORY_TARGET}) | From |",
        "| --- | ---: | ---: | --- |",
    ]
    for step, (size, seconds) in largest.items():
        power = GROWTH.get(step, 1)
        estimate = seconds * (ESTIMATE_AT / size) ** power
        peak, how = _memory(step, memories.get(step, []))
        growth = "time by the square of the size" if power == 2 else "time by the size"
        lines.append(
            f"| {step} | {_time(estimate)} | {_size(peak)} "
            f"| {size:,}, {growth}, {how} |"
        )
    cpu = results.get("machine_info", {}).get("cpu", {})
    if cpu:
        cores = cpu.get("count", "?")
        lines += [
            "",
            f"Measured on {cpu.get('brand_raw', 'a computer')}, {cores} cores.",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    """Print the report for a benchmarks results file, and return the exit code."""
    if len(argv) != 1:
        print(__doc__)
        return 2
    print(table(json.loads(read_text(argv[0]))), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
