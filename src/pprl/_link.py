"""Run ``pprl link``: link every site's hashes into groups, one group per patient.

The command checks all of its inputs before it writes any output. It reads
input files from ``data_folder`` and writes output to ``output_folder``.
The CLI passes the workspace for both.
"""

import contextlib
from array import array
from functools import partial
from itertools import combinations, permutations, repeat
from multiprocessing import shared_memory
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from anonlink.concurrency import split_to_chunks

# anonlink's compiled Dice coefficient. link calls it directly on hashes that
# it packs once (see _dice). It is private, but pinned along with anonlink.
from anonlink.similarities import _dice as _dice_c
from anonlink.solving import probabilistic_greedy_solve

from . import _messages as msg
from . import _pprl_file, _quality
from ._inputs import (
    check_hash_files,
    check_number_in_range,
    check_output,
    check_workers,
    checked_hash_files,
)
from ._io import display_name, for_writing_together
from ._messages import Label
from ._shared import LINKAGE_COLUMNS, SIMILARITY, STUDY_ID, Member
from ._ui import (
    ValidationContext,
    advance,
    finish_progress,
    info_log,
    note_user,
    ok_user,
    plan_steps,
    step,
    stoppable,
    warn_user,
)
from ._workers import worker_pool

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

    from bitarray import bitarray

    from ._inputs import HashFile

# Linkage compares each record with every record in the other files, in
# chunks of about CHUNK_COMPARISONS comparisons. When the total reaches
# PARALLEL_MIN_COMPARISONS, the chunks run in worker processes. Starting the
# workers takes about a second, so this only saves time at roughly 17,000
# records per site (with two sites). The results are the same either way.
PARALLEL_MIN_COMPARISONS = 300_000_000
CHUNK_COMPARISONS = 25_000_000

# The steps of link's progress bar. Reading gets a small fixed share. The
# other steps get their shares once the file sizes are known (see work).
# Comparing grows with the square of the number of records, so it takes up
# nearly all of a large linkage.
STEPS = ((msg.STEP_READING_HASHES, 2), (msg.STEP_COMPARING, 98))
# The time that grouping and writing take per record, expressed as the
# number of pairs that could be compared in the same time. Measured at
# 100,000 records per site on 8 cores.
GROUPING_PAIRS_PER_RECORD = 2_800
WRITING_PAIRS_PER_RECORD = 350

# Each file's hashes in the form anonlink's compiled Dice coefficient takes:
# one row of bytes per record, plus each row's count of set bits. In a worker
# process, _attach_hashes sets this to (the shared memory, the datasets).
_datasets: tuple[shared_memory.SharedMemory, list] | None = None


def _layout(hashes: list[list[bitarray]]) -> tuple[list[int], int]:
    """Return the files' sizes and the number of bytes in each hash.

    Every hash has the same length, which is a whole number of bytes (see
    check_hash_files).
    """
    sizes = [len(h) for h in hashes]
    nbytes = next((len(h[0]) // 8 for h in hashes if h), 0)
    return sizes, nbytes


def _rows(
    buffer: memoryview | bytearray | None, sizes: list[int], nbytes: int
) -> np.ndarray:
    """View ``buffer`` as one row of bytes for each hash in every file."""
    return np.ndarray((sum(sizes), nbytes), np.int8, buffer)


def _fill_rows(hashes: list[list[bitarray]], rows: np.ndarray) -> None:
    """Write each file's hashes into ``rows``, one file after the other.

    Each row holds the bytes of one bitarray, as anonlink's dice_coefficient
    takes them.
    """
    start = 0
    for h in hashes:
        if h:
            rows[start : start + len(h)] = np.frombuffer(b"".join(h), np.int8).reshape(
                len(h), -1
            )
        start += len(h)


def _datasets_of(
    rows: np.ndarray, sizes: list[int]
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return each file's rows with each row's count of set bits: [(rows, counts)]."""
    datasets, start = [], 0
    for size in sizes:
        part = rows[start : start + size]
        counts = np.bitwise_count(part.view(np.uint8)).sum(axis=1, dtype=np.uintc)
        datasets.append((part, counts))
        start += size
    return datasets


def _hashes_in_process(
    hashes: list[list[bitarray]],
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Pack each file's hashes in this process (see _datasets)."""
    sizes, nbytes = _layout(hashes)
    rows = _rows(bytearray(sum(sizes) * nbytes), sizes, nbytes)
    _fill_rows(hashes, rows)
    return _datasets_of(rows, sizes)


@contextlib.contextmanager
def _shared_hashes(
    hashes: list[list[bitarray]],
) -> Generator[tuple[str, list[int], int]]:
    """Pack each file's hashes into shared memory for the worker processes.

    Each worker attaches to the memory once, instead of receiving a copy of
    the hashes with every chunk. Yield (name, sizes, nbytes) for
    _attach_hashes.
    """
    sizes, nbytes = _layout(hashes)
    memory = shared_memory.SharedMemory(create=True, size=max(1, sum(sizes) * nbytes))
    try:
        _fill_rows(hashes, _rows(memory.buf, sizes, nbytes))
        yield memory.name, sizes, nbytes
    finally:
        memory.close()
        memory.unlink()


def _attach_hashes(name: str, sizes: list[int], nbytes: int) -> None:
    """Attach a worker process to the hashes that the main process shares.

    See _shared_hashes. The worker stays attached until it stops.
    """
    global _datasets  # noqa: PLW0603 (set once per worker process, as it starts)
    # track=False, because the main process frees the memory, not each worker.
    memory = shared_memory.SharedMemory(name, track=False)
    _datasets = (memory, _datasets_of(_rows(memory.buf, sizes, nbytes), sizes))


def _in_worker(task: Callable, *arguments: object) -> object:
    """Run ``task`` on the hashes this worker process is attached to."""
    if _datasets is None:  # Never: the worker attached as it started.
        raise AssertionError
    return task(_datasets[1], *arguments)


def comparisons(sizes: list[int]) -> int:
    """Count the pairs of records that a linkage of files of ``sizes`` compares.

    Each record is compared with every record in the other files.

    >>> comparisons([3, 4, 5])
    47
    """
    return sum(a * b for a, b in combinations(sizes, 2))


def _sampled_pairs(sizes: list[int]) -> int:
    """Count the pairs compared when finding the sample's best scores."""
    return sum(min(a, _quality.SAMPLE) * b for a, b in permutations(sizes, 2))


def work(sizes: list[int], *, report: bool) -> list:
    """List the steps link takes after reading files of ``sizes``.

    Each step is (words, pairs compared or the equivalent in time).
    """
    records = sum(sizes)
    return [
        (msg.STEP_COMPARING, comparisons(sizes)),
        (msg.STEP_GROUPING, GROUPING_PAIRS_PER_RECORD * records),
        *([(msg.STEP_BEST_SCORES, _sampled_pairs(sizes))] if report else []),
        (msg.STEP_WRITING_LINKAGE, WRITING_PAIRS_PER_RECORD * records),
    ]


@contextlib.contextmanager
def _comparing(
    hashes: list[list[bitarray]],
    workers: int,
    min_comparisons: int = PARALLEL_MIN_COMPARISONS,
) -> Generator[Callable]:
    """Yield a map(task, *arguments) function for comparing the packed hashes.

    The function runs a comparison ``task`` (see _hashes_in_process). It uses
    ``workers`` processes when there are at least ``min_comparisons``
    comparisons, and this process otherwise. The processes start once and
    serve every comparison in a linkage.
    """
    if workers == 1 or comparisons([len(h) for h in hashes]) < min_comparisons:
        datasets = _hashes_in_process(hashes)
        # zip is not strict here: a task's arguments may come from an endless
        # repeat().
        yield lambda task, *arguments: map(
            partial(task, datasets), *arguments, strict=False
        )
        return
    with (
        _shared_hashes(hashes) as shared,
        worker_pool(workers, (_attach_hashes, *shared)) as pool,
    ):
        yield lambda task, *arguments: pool.map(partial(_in_worker, task), *arguments)


def _dice(
    rows0: np.ndarray, rows1: np.ndarray, counts1: np.ndarray, threshold: float, k: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute anonlink's compiled Dice coefficient between rows0 and rows1.

    This calls it the same way anonlink's dice_coefficient does, comparing
    each row of rows0 with each row of rows1. Return (similarities, row in
    rows0, row in rows1) for each pair with similarity >= threshold, keeping
    at most k pairs for each row of rows0.
    """
    sims, i0, i1 = array("d"), array("I"), array("I")
    if len(rows0) and len(rows1):
        _dice_c.dice_many_to_many(
            rows0.reshape(-1),  # ty: ignore[invalid-argument-type] (any buffer)
            rows1.reshape(-1),  # ty: ignore[invalid-argument-type] (any buffer)
            len(rows0),
            len(rows1),
            counts1,  # ty: ignore[invalid-argument-type] (any buffer)
            rows0.shape[1],
            k,
            threshold,
            sims,
            i0,
            i1,
        )
    return (
        np.frombuffer(sims, np.float64),
        np.frombuffer(i0, np.uintc),
        np.frombuffer(i1, np.uintc),
    )


def _compare_chunk(  # noqa: PLR0917 (a pool maps it, passing arguments by position)
    datasets: list[tuple[np.ndarray, np.ndarray]],
    first: int,
    a0: int,
    a1: int,
    second: int,
    b0: int,
    b1: int,
    threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Find the candidate pairs between two chunks of records.

    The chunks are records a0 to a1 of file ``first`` and records b0 to b1
    of file ``second``. Return (similarities, records, records).
    """
    rows0, _ = datasets[first]
    rows1, counts1 = datasets[second]
    sims, r0, r1 = _dice(rows0[a0:a1], rows1[b0:b1], counts1[b0:b1], threshold, b1 - b0)
    return sims, r0 + np.uintc(a0), r1 + np.uintc(b0)


def _candidate_pairs(
    compare: Callable,
    sizes: list[int],
    threshold: float,
    chunk_comparisons: int = CHUNK_COMPARISONS,
) -> tuple:
    """Find every pair of records from different files with similarity >= threshold.

    The pairs are the same, in the same order, as anonlink's
    find_candidate_pairs returns: by similarity, highest first, then by file
    and record. The comparison runs in chunks, using ``compare`` (see
    _comparing).
    """
    chunks = list(split_to_chunks(chunk_comparisons, dataset_sizes=sizes))
    arguments = [
        (c0["datasetIndex"], *c0["range"], c1["datasetIndex"], *c1["range"])
        for c0, c1 in chunks
    ]
    sims, d0, d1, r0, r1 = [np.empty(0)], *([np.empty(0, np.uintc)] for _ in range(4))
    for (first, a0, a1, second, b0, b1), (s, rows0, rows1) in zip(
        arguments,
        compare(_compare_chunk, *zip(*arguments, strict=True), repeat(threshold)),
        strict=True,
    ):
        sims.append(s)
        d0.append(np.full(len(s), first, np.uintc))
        d1.append(np.full(len(s), second, np.uintc))
        r0.append(rows0)
        r1.append(rows1)
        advance((a1 - a0) * (b1 - b0))
    sims, d0, d1, r0, r1 = map(np.concatenate, (sims, d0, d1, r0, r1))
    order = np.lexsort((r1, r0, d1, d0, np.negative(sims)))
    return (
        array("d", sims[order].tobytes()),
        (array("I", d0[order].tobytes()), array("I", d1[order].tobytes())),
        (array("I", r0[order].tobytes()), array("I", r1[order].tobytes())),
    )


# The chain rule: two groups of linked records join when at least this share
# of the pairs between them score above the threshold. For example, with
# three or more sites, records at A and C that each match B but not each
# other are grouped (1 of 2 pairs). A fourth record that matches only C is not
# (1 of 3 pairs). This is anonlink's default. It is set here so that a change
# in anonlink can't alter it unnoticed.
CHAIN_AGREEMENT = 0.5


def link(
    hashes: list[str] | None = None,
    threshold: float | None = None,
    output: str | None = None,
    workers: int | None = None,
    sites: list[str] | None = None,
    *,
    report: str | None = None,
    secret_check: bool = True,
    provenance: dict | None = None,
    data_folder: Path | str,
    output_folder: Path | str,
) -> int:
    """Link hashes across multiple sites using Dice similarity.

    Read two or more hash files and compute the similarities between records
    at different sites. Resolve groups with probabilistic greedy solving.
    Write the matched groups to a linkage file, which names each hash file
    as an input. With ``report``, also write a linkage report for the
    coordinator.

    Parameters
    ----------
    hashes : list of str
        List of 2 or more hash file names (in data_folder).
    threshold : float
        Minimum Dice similarity for a match (0.0 to 1.0).
    output : str
        Output filename for linkage results.
    workers : int, optional
        The number of processes to compare records in. Defaults to all CPU
        cores. Large linkages are split across them, with the same result.
    sites : list of str, optional
        The site each hash file must be, in order.
    report : str, optional
        Output filename for the linkage report. With it, link reports its
        weak pairs and near misses around the threshold, and writes what it
        found to the report's ``quality`` member, for pprl review. The report
        holds only counts, never a record. A sweep does not report.
    secret_check : bool, optional
        Whether a reporting link runs the same-secret check first. The
        linkage agent's bundle says; a project that uses ``record_salt``
        turns it off, since salting each record makes the check fail.
    provenance : dict, optional
        The ``project`` and bundle ``set`` that every hash file must belong
        to, and that the linkage file is written for. Without it (as in a
        sweep on its own hash files), any files are linked, and the linkage
        file is written for the first hash file's provenance.

    Returns
    -------
    int
        0 on success, 1 on validation failure.

    Output schema
    -------------
    The linkage file's ``linkages`` member has the columns group_id, site,
    study_id, and similarity. The similarity is the group's strength, which
    is the score of its weakest pair. There is one row per record in each
    matched group. group_id counts up from 1, with groups ordered by their
    first (dataset, record) index, so the output is deterministic. Groups of
    a single record are left out.

    A group holds at most one record from each site, because each row of a
    site's file is a different patient. When two records from one site both
    match a third record, only the closer pair is grouped. Groups join by the
    chain rule (see CHAIN_AGREEMENT).
    """
    ctx = ValidationContext()
    check_number_in_range(ctx, Label.THRESHOLD, msg.THRESHOLD_INVALID, threshold)
    workers = check_workers(ctx, workers)
    linkages_file_path = check_output(
        ctx, Label.OUTPUT_FILE, "output", output, output_folder
    )
    report_path = None
    if report is not None:
        report_path = check_output(
            ctx, Label.REPORT_FILE, "report", report, output_folder
        )
    hash_files = check_hash_files(ctx, hashes, data_folder, sites, provenance)
    if ctx.conclude():
        return 1
    # These are None only after a failure, which conclude has reported.
    if threshold is None or linkages_file_path is None:
        raise AssertionError
    hash_files = checked_hash_files(hash_files)
    files = [hash_file.file for hash_file in hash_files]
    site_values = [hash_file.site for hash_file in hash_files]
    valid_hashes = [hash_file.hashes for hash_file in hash_files]
    provenance = provenance or {
        "project": files[0].header["project"],
        "set": files[0].header["set"],
    }

    sizes = [len(h) for h in valid_hashes]
    correlations = None
    if report_path and secret_check:
        correlations = _quality.bit_correlations(valid_hashes)
        _check_secrets(ctx, correlations, site_values)
        if ctx.conclude():
            return 1
    plan_steps(work(sizes, report=report_path is not None))
    stoppable("link", comparisons(sizes))
    candidate_pairs, groups, linkages, best = _grouped(
        hash_files, threshold, workers, sample=report_path is not None
    )
    linked = [_quality.datasets_in(group) for group in groups]
    quality = None
    if report_path is not None:
        quality = _quality.summary(
            site_values,
            sizes,
            threshold,
            candidate_pairs,
            groups,
            linked=linked,
            best=best,
            correlations=correlations,
            secret_check=secret_check,
        )
    step(msg.STEP_WRITING_LINKAGE)
    written = [p for p in (linkages_file_path, report_path) if p is not None]
    with for_writing_together(*written):
        _pprl_file.write(
            linkages_file_path,
            "linkages",
            {Member.LINKAGES: linkages},
            provenance=provenance,
            inputs=files,
        )
        if report_path is not None and quality is not None:
            _pprl_file.write(
                report_path,
                "linkage_report",
                {Member.QUALITY: quality},
                provenance=provenance,
                inputs=files,
            )
    finish_progress()
    ok_user(
        msg.WROTE_LINKAGES(groups=len(groups), output=display_name(linkages_file_path))
    )
    if report_path is not None:
        ok_user(msg.WROTE_LINKAGE_REPORT(output=display_name(report_path)))
    _report_pairs(linked, site_values)
    if quality is not None:
        _report_quality(quality)
    return 0


def _check_secrets(
    ctx: ValidationContext, correlations: dict, sites: list[str]
) -> None:
    """Check whether every pair of sites hashed with the same secret."""
    for (i, j), correlation in correlations.items():
        if not _quality.same_secret(correlation):
            ctx.fail(
                Label.SAME_SECRET,
                msg.LINK_OTHER_SECRET(
                    first=sites[i], second=sites[j], correlation=correlation
                ),
            )


def _grouped(
    hash_files: list[HashFile], threshold: float, workers: int, *, sample: bool
) -> tuple:
    """Compare each file's hashes and link them into groups.

    Return the candidate pairs (see _candidate_pairs), the groups of two or
    more records, and the linkage file's table of them (see _linkages). If
    ``sample`` is set, also return the sample for near misses (see
    _sampled_best); otherwise None. Any worker processes start once and serve
    both comparisons.
    """
    hashes = [hash_file.hashes for hash_file in hash_files]
    sizes = [len(h) for h in hashes]
    step(msg.STEP_COMPARING, comparisons(sizes))
    with _comparing(hashes, workers) as compare:
        candidate_pairs = _candidate_pairs(compare, sizes, threshold)

        step(msg.STEP_GROUPING)
        groups = probabilistic_greedy_solve(
            candidate_pairs, merge_threshold=CHAIN_AGREEMENT, deduplicated=True
        )
        # anonlink's compiled solver returns groups in an order that
        # depends on memory addresses, so it varies between runs. Sort
        # records within each group, then the groups themselves, so
        # group_ids are stable.
        groups = sorted(tuple(sorted(group)) for group in groups if len(group) > 1)
        linkages = _linkages(hash_files, candidate_pairs, groups)

        best = None
        if sample:
            step(msg.STEP_BEST_SCORES, _sampled_pairs(sizes))
            best = _sampled_best(compare, sizes, groups, workers)
    return candidate_pairs, groups, linkages, best


def _linkages(
    hash_files: list[HashFile], candidate_pairs: tuple, groups: list
) -> pd.DataFrame:
    """Build the linkage file's ``linkages`` member.

    There is one row for each record of each group. Groups are numbered from
    1 in order, and each row has its group's strength.
    """
    sizes = [len(hash_file.hashes) for hash_file in hash_files]
    strengths, _ = _quality.group_strengths(candidate_pairs, groups, sizes)
    rows = [
        (
            group_id,
            hash_files[dataset_idx].site,
            hash_files[dataset_idx].rows[STUDY_ID].iloc[record_idx],
            f"{strength:.3f}",
        )
        for group_id, (group, strength) in enumerate(
            zip(groups, strengths, strict=True), start=1
        )
        for dataset_idx, record_idx in group
    ]
    return pd.DataFrame(rows, columns=[*LINKAGE_COLUMNS, SIMILARITY])


def _sampled_best(
    compare: Callable, sizes: list[int], groups: list, workers: int
) -> dict:
    """Sample each site's records, with their best scores at each other site.

    For each site and each other site, take a random sample of the site's
    records: up to _quality.SAMPLE records, drawn afresh on each run. For each
    sampled record, find its best score at the other site, and whether it
    is linked to a record there. Return {(site, other): (records, scores,
    linked)}.
    """
    group_sites = {}
    for group in groups:
        held = _quality.datasets_in(group)
        for dataset, record in group:
            group_sites[(dataset, record)] = held
    rng = np.random.default_rng()
    best = {}
    for a, b in permutations(range(len(sizes)), 2):
        size = sizes[a]
        records = np.sort(rng.choice(size, min(size, _quality.SAMPLE), replace=False))
        scores = best_scores(compare, a, records, b, sizes[b], workers=workers)
        linked = np.array([b in group_sites.get((a, r), ()) for r in records])
        best[(a, b)] = (records, scores, linked)
    return best


def _best_chunk(  # noqa: PLR0917 (a pool maps it, passing arguments by position)
    datasets: list[tuple[np.ndarray, np.ndarray]],
    first: int,
    records: np.ndarray,
    second: int,
    b0: int,
    b1: int,
) -> np.ndarray:
    """Find the best score for each of ``records`` within a chunk of another file.

    ``records`` are in file ``first``. The chunk is records b0 to b1 of file
    ``second``.
    """
    rows0, _ = datasets[first]
    rows1, counts1 = datasets[second]
    scores, found, _ = _dice(
        np.ascontiguousarray(rows0[records]), rows1[b0:b1], counts1[b0:b1], 0.0, 1
    )
    best = np.zeros(len(records))
    best[found] = scores
    return best


def best_scores(
    compare: Callable,
    first: int,
    records: np.ndarray,
    second: int,
    size: int,
    *,
    workers: int,
    chunk_comparisons: int = CHUNK_COMPARISONS,
) -> np.ndarray:
    """Find the best score in file ``second`` for each of ``records`` in file ``first``.

    A record's best score is its highest Dice similarity with any of the
    ``size`` records of file ``second``. The comparison runs over chunks of
    ``second``, using ``compare`` (see _comparing).
    """
    best = np.zeros(len(records))
    if not len(records) or not size:
        return best
    # Make chunks of at least chunk_comparisons, and a few per worker. Each
    # chunk sends the sample's record numbers to its worker.
    rows = max(1, chunk_comparisons // len(records), -(-size // (4 * workers)))
    starts = range(0, size, rows)
    for start, found in zip(
        starts,
        compare(
            _best_chunk,
            repeat(first, len(starts)),
            repeat(records),
            repeat(second),
            starts,
            (min(start + rows, size) for start in starts),
        ),
        strict=True,
    ):
        np.maximum(best, found, out=best)
        advance(len(records) * (min(start + rows, size) - start))
    return best


def _report_pairs(linked: list, sites: list[str]) -> None:
    """Report how many records each pair of sites linked.

    ``linked`` gives each group's datasets. Warn about any pair of sites
    that linked no records.
    """
    for i, j in combinations(range(len(sites)), 2):
        count = _quality.linked_between(linked, i, j)
        names = {"first": sites[i], "second": sites[j]}
        if count:
            ok_user(msg.LINKED_PAIR(count=count, **names))
        else:
            warn_user(msg.NOTHING_LINKED_PAIR(**names))


def _report_quality(quality: dict) -> None:
    """Report what link found about the quality of its linked pairs.

    Show a summary, write the [INFO] lines to the linkage agent's own log,
    for troubleshooting, and show any warnings. pprl review reads the same
    figures from the linkage report, never from the log.
    """
    for line in _quality.reported(quality):
        note_user(line)
    for line in _quality.info_lines(quality):
        info_log(line)
    for warning in _quality.warned(quality):
        warn_user(warning)
