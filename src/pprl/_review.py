"""Run ``pprl review``: show a linkage's checklist and draw its histogram.

Both come from the linkage report, linkage_report.pprl, which pprl link
writes for the coordinator. The coordinator saves it in its workspace under
that name. The report holds only counts, so it never carries a patient's
record. It must belong to this workspace's project and, while the linkage
agent's bundle is still in the outbox, to that bundle's set. The histogram
is written as linkage_report.svg, beside the report.

The coordinator uses these to judge whether the linkage is plausible. pprl
review decides nothing itself.

The histogram shows the best scores of up to _quality.SAMPLE records per
site, all sites together, on a log scale. The threshold is drawn as a line,
and the review band is shaded.
pprl writes the SVG itself from a few rectangles, lines and labels, so it
needs no plotting library.
"""

import itertools
import math
from pathlib import Path

from . import _messages as msg
from . import _pprl_file, _quality
from ._bundle import bundle_name
from ._commands import LINKAGE_REPORT
from ._inputs import check_output, check_pprl_input
from ._io import display_name, for_writing
from ._messages import Label
from ._shared import Member, Role, outbox
from ._ui import (
    ValidationContext,
    fail,
    finish_progress,
    info_block,
    note_user,
    ok_user,
    warn_user,
)

# The histogram's file name, beside the report.
HISTOGRAM = Path(LINKAGE_REPORT).with_suffix(".svg").name
# The figure's size and the margins around its axes, in pixels.
WIDTH, HEIGHT = 720, 360
LEFT, RIGHT, TOP, BOTTOM = 64, 20, 56, 48
# Colors for the bars, the review band, the threshold, and the text and axes.
BAR_COLOR, BAND_COLOR, THRESHOLD_COLOR, INK = (
    "#2b5a9e",
    "#f0b46a",
    "#c0392b",
    "#1d1d1f",
)
# The bottom of the log scale. It is below 1, so a bin with one record still
# gets a visible bar.
FLOOR = 0.5


def _bundle_set(folder: Path) -> str | None:
    """Return the set of the linkage agent's bundle in the outbox, if it is there.

    The coordinator may have deleted it once it was sent. Then the report's
    project is still checked, but not its set.
    """
    path = folder / outbox(Role.LINKAGE, bundle_name(Role.LINKAGE))
    try:
        return _pprl_file.read_header(path)["set"]
    except FileNotFoundError, _pprl_file.PprlFileError:
        return None


def review(project: str, folder: Path) -> int:
    """Run ``pprl review``: show the checklist and draw the histogram.

    Read the linkage report in the coordinator's workspace ``folder``, for
    ``project``. Write the histogram beside it. Return the exit code.
    """
    if not (folder / LINKAGE_REPORT).is_file():
        return fail(msg.REVIEW_NOT_FOUND(name=LINKAGE_REPORT))
    ctx = ValidationContext()
    report = check_pprl_input(
        ctx,
        Label.LINKAGE_REPORT,
        LINKAGE_REPORT,
        folder,
        "linkage_report",
        provenance={"project": project, "set": _bundle_set(folder)},
    )
    output = check_output(ctx, Label.OUTPUT_FILE, "output", HISTOGRAM, folder)
    if ctx.conclude():
        return 1
    if report is None or output is None:  # Only after a failure
        raise AssertionError
    quality = report.members[Member.QUALITY]
    try:
        drawn = histogram_svg(quality)
    except KeyError, TypeError, ValueError:
        return fail(msg.REVIEW_UNREADABLE(name=LINKAGE_REPORT))
    with for_writing(output) as handle:
        handle.write(drawn)
    finish_progress()
    show_checklist(quality)
    ok_user(msg.REVIEW_WROTE(name=display_name(output)))
    return 0


def show_checklist(quality: dict) -> None:
    """Show a linkage report's checklist, in the same order as the coordinator's guide.

    pprl inspect shows it too.
    """
    for pair in quality["pairs"]:
        _show_secret(quality, pair)
    for pair in quality["pairs"]:
        first, second = pair["sites"]
        linked = pair["linked"]
        first_records = pair["records"][first]
        second_records = pair["records"][second]
        note_user(
            msg.REVIEW_PAIR(
                first=first,
                second=second,
                linked=linked,
                first_share=linked / first_records if first_records else 0,
                second_share=linked / second_records if second_records else 0,
            )
        )
    heading, rows = _best_scores(quality)
    note_user(heading)
    info_block(rows)
    for pair in quality["pairs"]:
        _show_false(pair)
    warnings = _quality.warned(quality)
    for warning in warnings:
        warn_user(msg.REVIEW_WARNING(text=warning.partition("\n")[0]))
    if not warnings:
        ok_user(msg.REVIEW_NO_WARNINGS)


def _show_secret(quality: dict, pair: dict) -> None:
    """Show what the same-secret check found for a pair of sites."""
    first, second = pair["sites"]
    correlation = pair["bit_correlation"]
    if not quality["secret_check"]:
        note_user(msg.REVIEW_SECRET_OFF(first=first, second=second))
    elif correlation is None:
        note_user(msg.REVIEW_SECRET_UNCHECKED(first=first, second=second))
    else:
        ok_user(
            msg.REVIEW_SAME_SECRET(
                first=first, second=second, correlation=float(correlation)
            )
        )


def _show_false(pair: dict) -> None:
    """Show the estimate of false pairs for a pair of sites, with its 95% range."""
    first, second = pair["sites"]
    false = pair["false"]
    if false["estimate"] is None:
        note_user(msg.REVIEW_MOSTLY_FALSE(first=first, second=second))
        return
    shown = msg.REVIEW_FALSE if false["high"] is not None else msg.REVIEW_FALSE_OPEN
    note_user(
        shown(
            first=first,
            second=second,
            false=false["estimate"],
            low=false["low"],
            high=false["high"],
        )
    )


def _best_scores(quality: dict) -> tuple[str, list[tuple[str, str]]]:
    """Return the heading and rows of a table of best scores around the review band.

    Each row gives a range of scores, its count and its share. The "and up"
    row is left out when the review band reaches 1, since it would always be
    empty.
    """
    counts = quality["best"]
    low, high = quality["review_band"]
    threshold = quality["threshold"]
    total = sum(counts)
    edges = [0, _bin(low), _bin(threshold), _bin(high), len(counts)]
    labels = [
        msg.REVIEW_BELOW(low=low),
        msg.REVIEW_NEAR(low=low, threshold=threshold),
        msg.REVIEW_WEAK(threshold=threshold, high=high),
        msg.REVIEW_ABOVE(high=high),
    ]
    rows = []
    for label, (start, end) in zip(labels, itertools.pairwise(edges), strict=True):
        count = sum(counts[start:end])
        rows.append((label, f"{count:>7,}  {count / (total or 1):>6.1%}"))
    if high >= 1.0:
        rows.pop()
    every = all(
        near["sample"] == pair["records"][site]
        for pair in quality["pairs"]
        for site, near in pair["near"].items()
    )
    scope = msg.REVIEW_ALL if every else msg.REVIEW_SAMPLED(sample=_quality.SAMPLE)
    return msg.REVIEW_BEST(count=total, scope=scope), rows


def _bin(score: float) -> int:
    """Return the bin a score falls in. A score of 1 returns the last bin's end.

    >>> _bin(0.75), _bin(1.0)
    (75, 100)
    """
    return math.floor(score / _quality.BIN + 1e-9)


def histogram_svg(quality: dict) -> str:
    """Draw an SVG histogram of a linkage's best scores, from its ``quality``."""
    counts = [int(count) for count in quality["best"]]
    band_low, band_high = (float(edge) for edge in quality["review_band"])
    threshold = float(quality["threshold"])
    low = _lowest(band_low, counts)
    left, top = LEFT, TOP
    width, height = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM
    decades = max(1, math.ceil(math.log10(max(counts, default=1) or 1)))

    def x(score: float) -> float:
        return left + width * (score - low) / (1.0 - low)

    def y(count: int) -> float:
        share = (math.log10(count) - math.log10(FLOOR)) / (decades - math.log10(FLOOR))
        return top + height * (1 - share)

    def label(text_x: float, text_y: float, text: str, extra: str = "") -> str:
        return (
            f'<text x="{text_x:.1f}" y="{text_y:.1f}" fill="{INK}"{extra}>{text}</text>'
        )

    bottom = top + height
    parts = [
        (
            f'<rect x="{x(band_low):.1f}" y="{top}" '
            f'width="{x(band_high) - x(band_low):.1f}" height="{height}" '
            f'fill="{BAND_COLOR}" fill-opacity="0.3"/>'
        ),
    ]
    for i, count in enumerate(counts):
        edge = i * _quality.BIN
        if count and edge >= low - _quality.BIN / 2:
            parts.append(
                f'<rect x="{x(edge):.1f}" y="{y(count):.1f}" '
                f'width="{x(edge + _quality.BIN) - x(edge):.1f}" '
                f'height="{bottom - y(count):.1f}" fill="{BAR_COLOR}"/>'
            )
    parts += [
        (
            f'<line x1="{x(threshold):.1f}" y1="{top}" x2="{x(threshold):.1f}" '
            f'y2="{bottom}" stroke="{THRESHOLD_COLOR}" stroke-width="1.5"/>'
        ),
        (
            f'<path d="M{left},{top} V{bottom} H{left + width}" fill="none" '
            f'stroke="{INK}"/>'
        ),
        label(left, 22, msg.REVIEW_TITLE, ' font-size="14" font-weight="600"'),
        label(
            left,
            40,
            f"{msg.REVIEW_THRESHOLD(threshold=threshold)} · "
            f"{msg.REVIEW_BAND(low=band_low, high=band_high)}",
        ),
        label(
            left + width / 2,
            bottom + 38,
            msg.REVIEW_SCORE,
            ' text-anchor="middle"',
        ),
        (
            f'<text transform="translate(16,{top + height / 2:.1f}) rotate(-90)" '
            f'text-anchor="middle" fill="{INK}">'
            f"{msg.REVIEW_RECORDS(sample=_quality.SAMPLE)}</text>"
        ),
    ]
    for tick in _ticks(low):
        parts.append(
            label(x(tick), bottom + 18, f"{tick:.2f}", ' text-anchor="middle"')
        )
    for decade in range(decades + 1):
        parts.append(
            label(left - 6, y(10**decade) + 4, f"{10**decade:,}", ' text-anchor="end"')
        )
    return "\n".join(
        [
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
                f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" '
                'font-family="system-ui, sans-serif" font-size="11">'
            ),
            f'<rect width="{WIDTH}" height="{HEIGHT}" fill="white"/>',
            *parts,
            "</svg>",
            "",
        ]
    )


def _lowest(band_low: float, counts: list[int]) -> float:
    """Return where the axis starts.

    The axis starts just below the lowest score found, or at the review band,
    whichever is lower.
    >>> _lowest(0.75, [0] * 60 + [3] + [0] * 39)
    0.58
    """
    first = next((i for i, count in enumerate(counts) if count), 100)
    return max(0.0, round(min(band_low, first * _quality.BIN) - 0.02, 2))


# Axes up to this length (in score units) get a tick every 0.05. Longer axes
# get a tick every 0.1.
_SHORT_AXIS = 0.3


def _ticks(low: float) -> list:
    """Return the tick marks for an axis from ``low`` to 1.

    Ticks are every 0.05, or every 0.1 on a long axis.

    >>> _ticks(0.78)
    [0.8, 0.85, 0.9, 0.95, 1.0]
    """
    step = 5 if 1.0 - low <= _SHORT_AXIS else 10
    return [n / 100 for n in range(0, 101, step) if n / 100 >= low - 1e-9]
