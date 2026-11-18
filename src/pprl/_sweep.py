"""Run ``pprl sweep``: search over thresholds and weights for linkage settings.

The coordinator runs ``pprl sweep`` in their workspace on synthetic data,
to choose the project's threshold and the fields' weights. It has two
modes: a threshold sweep on existing hash files, and a grid search over
sets of weights, which re-hashes the patient files for each set.
"""

from itertools import pairwise
from math import isnan, nan
from pathlib import Path

import pandas as pd

from . import _hash, _link, _pprl_file, _wording
from . import _messages as msg
from ._config import load_config
from ._inputs import (
    check_fields,
    check_fill_ratio,
    check_hash_files,
    check_hash_length,
    check_id_column,
    check_input_csv,
    check_output,
    check_patients,
    check_project_salt,
    check_record_salt,
    check_true_false,
    check_workers,
    check_xor_folds,
    checked_hash_files,
    new_secret,
)
from ._io import (
    display_name,
    read_csv_frame,
    scratch,
    workspace,
    write_csv_frame,
)
from ._messages import Kind, Label
from ._metrics import (
    count_true_pairs,
    density_stats,
    f1_score,
    precision_recall,
)
from ._pprl_file import file_name
from ._schema import field_weights
from ._shared import STUDY_ID, Member, is_share
from ._ui import (
    ValidationContext,
    finish_progress,
    note_user,
    ok_user,
    plan_steps,
    progress,
    show,
    silenced,
    step,
    stoppable,
)

# A grid sweep writes its own files for this made-up project and bundle set.
# The same value is used as each patient file's site name.
SWEEP_PROJECT = "00000000-0000-0000-0000-000000000000"
# The prefix of the result columns that hold each field's weight.
WEIGHT = "weight_"


# The steps of sweep's progress bar. Sweep prepares first. Then, for each set
# of weights, it prepares again, hashes each site's records, and links at
# each threshold (see _planned). Each step's share of the bar is the number of
# pairs it compares, or the equivalent amount of time.
STEPS = ((msg.STEP_PREPARING, 1), (msg.STEP_LINKING, 99))
PREPARING_PAIRS_PER_RECORD = 1_000
HASHING_PAIRS_PER_RECORD = 11_000


def _linking_pairs(sizes: list[int]) -> int:
    """Count the pairs that one linkage compares, for files of the given ``sizes``."""
    return sum(pairs for _, pairs in _link.work(sizes, report=False))


def _site(i: int) -> str:
    return f"file_{i + 1}"


def _provenance(i: int) -> dict:
    """Make up a project and bundle set for the sweep's ``i``th patient file.

    The sweep uses them for the files it writes for itself.
    """
    return {"project": SWEEP_PROJECT, "set": SWEEP_PROJECT, "site": _site(i)}


# Every key a sweep config may have, in either mode.
SWEEP_KEYS = (
    "hashes",
    "patients",
    "id",
    "fields",
    "hash_length",
    "fill_ratio",
    "xor_folds",
    "project_salt",
    "record_salt",
    "thresholds",
    "output",
    "assessment",
    "workers",
)


def _threshold_problem(thresholds: object) -> str | None:
    """Check that ``thresholds`` is a list of numbers from 0 to 1.

    Return a message if it is not, or None if it is.

    >>> _threshold_problem([0.9, 0.95]) is None
    True
    >>> _threshold_problem([0.9, 1.5]).splitlines()[0]
    '`thresholds` should be a list of numbers from 0.0 to 1.0, but is [0.9, 1.5].'
    """
    if (
        isinstance(thresholds, list)
        and thresholds
        and all(is_share(t) for t in thresholds)
    ):
        return None
    return msg.THRESHOLDS_INVALID(value=_wording.shown(thresholds))


# ---------------------------------------------------------------------------
# Config expansion
# ---------------------------------------------------------------------------


def _weightings(ctx: ValidationContext, config: dict) -> list[dict] | None:
    """Check a grid sweep's `fields`, which is one set of weights or a list of them.

    Return the list, or None after recording the problems in ctx.

    Example config YAML:
        fields:
          - {name: 0.50, dob: {weight: 0.25, type: date}, city: 0.25}
          - {name: 0.45, dob: {weight: 0.20, type: date}, city: 0.35}
    """
    if "fields" not in config:
        ctx.fail(
            Label.SETTINGS, msg.SWEEP_MISSING_KEY(key="fields", mode="patient files")
        )
        return None
    fields = config["fields"]
    weightings = fields if isinstance(fields, list) and fields else [fields]
    if any(check_fields(ctx, each) is None for each in weightings):
        return None
    return weightings


def _format_schema_label(fields: dict) -> str:
    """Label a grid entry compactly by its weights."""
    return ", ".join(f"{name}={w}" for name, w in field_weights(fields).items())


# ---------------------------------------------------------------------------
# Shared sweep helpers
# ---------------------------------------------------------------------------


def _sweep_thresholds(
    hashes: list[str],
    thresholds: list[float],
    n: int | None,
    tmp: Path,
    workers: int | None = None,
    *,
    data_folder: Path | str | None = None,
) -> list[dict]:
    """Run link at each threshold and collect linkage metrics.

    Parameters
    ----------
    hashes : list of str
        Hash filenames (in ``data_folder``, or else in ``tmp``).
    thresholds : list of float
        Thresholds to evaluate (will be sorted internally).
    n : int or None
        Ground-truth pair count. Enables precision/recall/f1 when set.
    tmp : Path
        Working directory, for the linkage files.
    data_folder : Path, optional
        The folder of the hash files (the workspace), if it is not ``tmp``.

    Returns
    -------
    list of dict
        Result dicts with keys: threshold, matches, and (if n is set)
        precision, recall, f1, tp, fp, fn.
    """
    results = []
    for t in sorted(thresholds):
        linkage_output = f"linkages_{t:.6f}.pprl"
        step(msg.STEP_LINKING)
        with silenced(), progress("link", _link.STEPS):
            rc = _link.link(
                hashes=list(hashes),
                threshold=t,
                output=linkage_output,
                workers=workers,
                data_folder=data_folder or tmp,
                output_folder=tmp,
            )
        if rc != 0:  # The inputs were validated, so this would be a bug.
            reason = f"Linkage failed at threshold {t}"
            raise RuntimeError(reason)

        linkages = _pprl_file.read(tmp / linkage_output).members[Member.LINKAGES]
        match_count = linkages["group_id"].nunique()
        row = {
            "threshold": t,
            "matches": match_count,
        }

        if n is not None:
            pr = precision_recall(linkages, n)
            row.update(pr)
            row["f1"] = f1_score(pr["precision"], pr["recall"])

        results.append(row)

    return results


def _format_best_result(best: dict) -> str:
    """Format the threshold and weights of a best-result row for display."""
    weights = ", ".join(
        f"{key.removeprefix(WEIGHT)}={value:g}"
        for key, value in best.items()
        if key.startswith(WEIGHT) and not isnan(value)
    )
    return _wording.supplied(f"threshold={best['threshold']}, {weights}")


def _show_sweep_table(results: list[dict], *, assessment: bool) -> None:
    """Show a formatted table of sweep results."""
    threshold, matches, precision, recall = msg.SWEEP_TABLE_HEADINGS
    header = f"{threshold:<12} {matches:<10}"
    rule = f"{'-' * 12} {'-' * 10}"
    if assessment:
        header += f" {precision:<11} {recall:<10}"
        rule += f" {'-' * 11} {'-' * 10}"
    show(header.rstrip())
    show(rule)
    for row in results:
        line = f"{row['threshold']:<12.3f} {row['matches']:<10}"
        if assessment and "precision" in row:
            line += f" {row['precision']:<11.2%} {row['recall']:<10.2%}"
        show(line.rstrip())


def _finish_sweep(count: int, out_path: Path | None) -> None:
    """Show the closing line of a sweep."""
    if out_path is None:
        ok_user(msg.SWEPT(count=count))
    else:
        ok_user(msg.SWEPT_TO_FILE(count=count, output=display_name(out_path)))


# ---------------------------------------------------------------------------
# Sweep entry point
# ---------------------------------------------------------------------------


def sweep(config_path: str) -> int:
    """Run a search over Bloom filter linkage settings.

    For each set of weights, hash the patient files and measure their
    density. Then try each threshold and compute linkage quality metrics.

    Config YAML for grid search mode (see _weightings):
        patients: ["site_a.csv", "site_b.csv"]
        id: person_id
        fields:
          name: 0.50
          dob: {weight: 0.25, type: date}
          city: 0.25
        hash_length: 1024
        fill_ratio: 1.0
        thresholds: [0.80, 0.85, 0.90, 0.95, 0.975, 0.99]
        output: sweep_results.csv
        assessment: true

    Config YAML for threshold-only mode, on hash files:
        hashes: ["site_a.hashes.pprl", "site_b.hashes.pprl"]
        thresholds: [0.80, 0.85, 0.90, 0.95, 0.975, 0.99]
        output: sweep_results.csv

    With ``assessment: true`` (grid mode only), precision and recall treat
    the ``id`` column as the ground truth. Records at different sites with
    the same id are the same person. This
    holds for patient files that synth wrote with shared person ids. A
    threshold sweep cannot assess, because hash files have study_ids, which
    pprl clean draws afresh at each site.

    A grid sweep hashes with a new secret and then deletes the hashes. The
    choice of secret does not affect how well records link.
    """
    config = load_config(config_path, SWEEP_KEYS)
    if isinstance(config, int):
        return config

    # Choose the mode: threshold only (hash files) or grid search (patient
    # files and weights).
    if "hashes" in config and "patients" not in config:
        return _run_sweep_hashes(config, workspace())
    return _run_sweep_grid(config, workspace())


# ---------------------------------------------------------------------------
# Threshold sweep on pre-hashed files (the linkage agent's side)
# ---------------------------------------------------------------------------


def _run_sweep_hashes(config: dict, folder: Path) -> int:
    """Run a threshold sweep on existing hash files (the linkage agent's side).

    The inputs are checked the same way link checks them, so a sweep never
    links files that link would reject.
    """
    ctx = ValidationContext()
    hashes = config.get("hashes")

    thresholds = config.get("thresholds")
    problem = _threshold_problem(thresholds)
    if problem:
        ctx.fail(Label.THRESHOLDS, problem)

    output = config.get("output")
    if "assessment" in config:
        ctx.fail(Label.SETTINGS, msg.SWEEP_NO_ASSESSMENT())
    out_path = None
    if output is not None:
        out_path = check_output(ctx, Label.OUTPUT_FILE, "output", output, folder)

    hash_files = check_hash_files(ctx, hashes, folder)
    workers = check_workers(ctx, config.get("workers"))
    if ctx.conclude():
        return 1
    # Never: check_hash_files and _threshold_problem checked these above.
    if not isinstance(thresholds, list) or not isinstance(hashes, list):
        raise TypeError(thresholds, hashes)
    hash_files = checked_hash_files(hash_files)

    sizes = [len(hash_file.hashes) for hash_file in hash_files]
    stoppable("link", _link.comparisons(sizes) * len(thresholds))
    plan_steps([(msg.STEP_LINKING, _linking_pairs(sizes))] * len(thresholds))
    with scratch(folder) as tmp:
        results = _sweep_thresholds(
            hashes, thresholds, None, tmp, workers, data_folder=folder
        )

    finish_progress()
    _show_sweep_table(results, assessment=False)

    if out_path is not None:
        write_csv_frame(pd.DataFrame(results), out_path)

    _finish_sweep(len(results), out_path)
    return 0


# ---------------------------------------------------------------------------
# Grid search over weights with re-hashing
# ---------------------------------------------------------------------------


def _check_grid_config(ctx: ValidationContext, config: dict, folder: Path) -> tuple:
    """Check every setting and input file of a grid sweep, recording problems in ctx.

    The patient files are checked the same way hash checks them, except that
    extra columns are allowed, since only the schema's fields are hashed.
    This way hashing cannot fail once the sweep has started.
    """
    patients = config.get("patients")
    if not (isinstance(patients, list) and len(patients) > 1):
        ctx.fail(
            Label.SETTINGS, msg.SWEEP_PATIENTS_NOT_LIST(value=_wording.shown(patients))
        )
        patients = []
    problem = _threshold_problem(config.get("thresholds"))
    if problem:
        ctx.fail(Label.THRESHOLDS, problem)
    assessment = None
    if "assessment" not in config:
        ctx.fail(
            Label.SETTINGS,
            msg.SWEEP_MISSING_KEY(key="assessment", mode="patient files"),
        )
    else:
        assessment = check_true_false(ctx, config, "assessment")
    workers = check_workers(ctx, config.get("workers"))
    grid = _weightings(ctx, config)
    for fields in grid or [None]:
        check_hash_length(ctx, config.get("hash_length"), fields)
    check_fill_ratio(ctx, config.get("fill_ratio"))
    check_xor_folds(ctx, config.get("xor_folds", 0))
    check_project_salt(ctx, config.get("project_salt"))
    for fields in grid or [None]:
        check_record_salt(ctx, config.get("record_salt"), fields)
    id_ok = all(
        check_id_column(ctx, config.get("id"), each) for each in (grid or [None])
    )

    out_path = None
    if config.get("output") is not None:
        out_path = check_output(
            ctx, Label.OUTPUT_FILE, "output", config["output"], folder
        )

    sizes = []
    if grid and id_ok:
        for p in patients:
            _, df = check_input_csv(
                ctx,
                (Label.PATIENTS_FILE, Label.PATIENTS_CONTENT),
                "patients",
                Kind.PATIENTS,
                p,
                folder=folder,
            )
            sizes.append(0 if df is None else len(df))
            for fields in grid:
                check_patients(
                    ctx,
                    df,
                    p,
                    fields,
                    ignore_other_columns=True,
                    id_column=config["id"],
                )
    return patients, sizes, grid, assessment, workers, out_path


def _planned(sizes: list[int], weightings: int, thresholds: int) -> list:
    """Plan a grid sweep's remaining steps, once it has read its patient files."""
    records = sum(sizes)
    linking = _linking_pairs(sizes)
    return [
        (msg.STEP_PREPARING, PREPARING_PAIRS_PER_RECORD * records),
        *((msg.STEP_HASHING, HASHING_PAIRS_PER_RECORD * size) for size in sizes),
        *[(msg.STEP_LINKING, linking)] * thresholds,
    ] * weightings


def _run_sweep_grid(config: dict, folder: Path) -> int:
    """Run a grid search over sets of weights, re-hashing for each set."""
    ctx = ValidationContext()
    patients, sizes, grid, assessment, workers, out_path = _check_grid_config(
        ctx, config, folder
    )
    if ctx.conclude():
        return 1
    plan_steps(_planned(sizes, len(grid), len(config["thresholds"])))
    stoppable(
        "link",
        _link.comparisons(sizes) * len(config["thresholds"]) * len(grid),
    )
    secret = new_secret()
    thresholds = sorted(config["thresholds"])
    id_column = config["id"]

    n = None
    if assessment:
        n = count_true_pairs(
            [read_csv_frame(Path(folder) / p) for p in patients], id_column
        )

    results = []
    for index, fields in enumerate(grid, 1):
        place = {
            "index": index,
            "total": len(grid),
            "label": _wording.supplied(_format_schema_label(fields)),
        }
        step(msg.STEP_PREPARING)
        with scratch(folder) as tmp:
            _write_clean_files(patients, id_column, fields, folder, tmp)
            hash_files = _hashed(len(patients), secret, fields, config, tmp)
            row_base = _schema_columns(fields) | _density_columns(hash_files, tmp)
            threshold_results = _sweep_thresholds(
                hash_files, thresholds, n, tmp, workers
            )

        _add_marginal_rates(threshold_results)
        results.extend(row_base | row for row in threshold_results)
        if assessment:
            best_f1 = max(r["f1"] for r in threshold_results)
            note_user(msg.SWEEP_CONFIG_BEST_F1(f1=best_f1, **place))
        else:
            note_user(msg.SWEEP_CONFIG_DONE(**place))

    if out_path is not None:
        _write_grid_results(results, out_path)
    finish_progress()
    show()
    _report_best(results, assessment=assessment)
    _finish_sweep(len(thresholds) * len(grid), out_path)
    return 0


def _write_clean_files(
    patients: list[str], id_column: str, fields: dict, folder: Path, tmp: Path
) -> None:
    """Write each patient file to ``tmp`` in the format pprl clean writes.

    Keep only the schema's fields, and use the file's ids as study_ids so the
    assessment can compare them.
    """
    for i, p in enumerate(patients):
        df = read_csv_frame(Path(folder) / p)
        df = df[[id_column, *fields]].rename(columns={id_column: STUDY_ID})
        _pprl_file.write(
            tmp / file_name("clean", _site(i)),
            "clean",
            {Member.PATIENTS: df},
            provenance=_provenance(i),
        )


def _hashed(
    count: int, secret: str, fields: dict, config: dict, tmp: Path
) -> list[str]:
    """Hash each of the ``count`` clean files in ``tmp`` for one grid entry.

    The files are those ``_write_clean_files`` wrote. Hashing uses the grid
    entry's weights and the hashing settings in ``config``. Return the hash
    files' names, in ``tmp``.
    """
    hash_files = []
    for i in range(count):
        hash_output = file_name("hashes", _site(i))
        step(msg.STEP_HASHING)
        with silenced(), progress("hash", _hash.STEPS):
            rc = _hash.hash_records(
                patients=file_name("clean", _site(i)),
                secret=secret,
                fields=fields,
                output=hash_output,
                hash_length=config["hash_length"],
                fill_ratio=config["fill_ratio"],
                xor_folds=config.get("xor_folds", 0),
                project_salt=config.get("project_salt"),
                record_salt=config.get("record_salt"),
                provenance=_provenance(i),
                data_folder=tmp,
                output_folder=tmp,
            )
        if rc != 0:  # The inputs were validated, so this would be a bug.
            label = _format_schema_label(fields)
            reason = f"Hashing failed for configuration {label}"
            raise RuntimeError(reason)
        hash_files.append(hash_output)
    return hash_files


def _schema_columns(fields: dict) -> dict:
    """Put a grid entry's weights in the first columns of its result rows."""
    return {f"{WEIGHT}{name}": w for name, w in field_weights(fields).items()}


def _density_columns(hash_files: list[str], tmp: Path) -> dict:
    """Measure the density of a grid entry's hashes, as result columns.

    The density covers all of the entry's files together.
    """
    combined_hashes = pd.concat(
        [_pprl_file.read(tmp / h).members[Member.HASHES] for h in hash_files],
        ignore_index=True,
    )
    density = density_stats(combined_hashes)
    return {
        "density_mean": density["density_mean"],
        "density_min": density["density_min"],
        "density_max": density["density_max"],
    }


def _add_marginal_rates(threshold_results: list[dict]) -> None:
    """Add each threshold's marginal match rate to its row.

    The rate is the number of matches lost per unit of threshold between this
    threshold and the next (a finite difference). The last row gets NaN.
    """
    for row, following in pairwise(threshold_results):
        dt = following["threshold"] - row["threshold"]
        lost = row.get("matches", 0) - following.get("matches", 0)
        row["marginal_match_rate"] = lost / dt if dt > 0 else nan
    if threshold_results:
        threshold_results[-1]["marginal_match_rate"] = nan


def _write_grid_results(results: list[dict], out_path: Path) -> None:
    """Write a grid sweep's result rows to the CSV file ``out_path``."""
    results_df = pd.DataFrame(results)
    schema_cols = [c for c in results_df.columns if c.startswith(WEIGHT)]
    linkage_cols = ["threshold", "matches", "precision", "recall", "f1"]
    security_cols = ["density_mean", "density_min", "density_max"]
    signal_cols = ["marginal_match_rate"]
    desired_order = schema_cols + linkage_cols + security_cols + signal_cols
    ordered_cols = [c for c in desired_order if c in results_df.columns]
    write_csv_frame(results_df[ordered_cols], out_path)


def _report_best(results: list[dict], *, assessment: bool) -> None:
    """Show the best of a grid sweep's results.

    With an assessment, the best result has the highest F1. Without one, it
    is the result that the marginal match rate suggests.
    """
    if assessment:
        best = max(results, key=lambda r: r["f1"])
        ok_user(
            msg.SWEEP_BEST(
                f1=best["f1"],
                precision=best["precision"],
                recall=best["recall"],
                settings=_format_best_result(best),
            )
        )
    else:
        rated = [r for r in results if not isnan(r["marginal_match_rate"])]
        if rated:  # A suggestion needs at least two thresholds.
            best = max(rated, key=lambda r: r["marginal_match_rate"])
            ok_user(
                msg.SWEEP_SUGGESTED(
                    settings=_format_best_result(best),
                    matches=int(best["matches"]),
                )
            )
