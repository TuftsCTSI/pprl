"""Merge one table the way pprl 3.0.0.dev0 did, one group at a time.

This is the reference that the faster merge in pprl must match, row for
row (the merge tests compare them). It is copied unchanged, except for this
docstring.
"""

import pandas as pd


def _filled(series: pd.Series) -> pd.Series:
    """Which values are present: not missing, and not empty."""
    return series.notna() & (series != "")


def _as_dates(series: pd.Series) -> pd.Series:
    """ISO 8601 dates or datetimes (yyyy-mm-dd, or yyyy-mm-dd HH:MM:SS)."""
    return pd.to_datetime(series, format="ISO8601")


def _criteria(rank_by: list[dict]) -> list[tuple[str, dict]]:
    """``rank_by`` as ``(strategy, settings)`` pairs, in order."""
    return [next(iter(item.items())) for item in rank_by]


def _ranks(
    rows: pd.DataFrame, strategy: str, settings: dict, site_order: list[str]
) -> pd.Series:
    """How each row ranks under one strategy: higher wins.

    An empty value of the compared column ranks below every value (NaN, or
    NaT for dates): a row without a date is older than any dated row.
    take_first_site ranks each row by its site, the first highest.
    """
    if strategy == "take_first_site":
        return -rows["__site__"].map(site_order.index)
    compared = rows[settings["column"]]
    compared = compared.where(_filled(compared))
    if strategy == "take_highest":
        return pd.to_numeric(compared)
    if strategy == "take_latest":
        return _as_dates(compared)
    if strategy == "take_earliest":
        dates = _as_dates(compared)
        return -(dates - dates.min())
    if strategy == "take_top_ordinal":
        scale = [str(v) for v in settings["scale"]]
        return compared.map(lambda v: scale.index(v) if v in scale else float("nan"))
    msg = f"Unknown strategy: {strategy!r}"
    raise ValueError(msg)


def _winning_row(
    group: pd.DataFrame,
    column: str,
    criteria: list[tuple[str, dict]],
    site_order: list[str],
) -> object:
    """Find the index label of the row whose ``column`` value wins, or None.

    Only rows with a value in ``column`` take part. Each strategy in turn
    keeps the rows it ranks highest (see ``_ranks``), so a tie under one
    goes to the next; a tie under all of them goes to the first site in
    ``site_order``. Rows that one strategy cannot rank at all stay tied.
    """
    candidates = group[_filled(group[column])]
    for strategy, settings in criteria:
        if len(candidates) <= 1:
            break
        ranks = _ranks(candidates, strategy, settings, site_order)
        best = ranks.max()
        if not pd.isna(best):
            candidates = candidates[ranks == best]
    if len(candidates) <= 1:
        return candidates.index[0] if len(candidates) else None
    for site in site_order:
        match = candidates[candidates["__site__"] == site]
        if len(match) > 0:
            return match.index[0]
    return candidates.index[0]


def _lead_rules(column_config: dict) -> dict:
    """List the columns with ``rank_by``, each with the columns tethered to it."""
    return {
        name: {
            "criteria": _criteria(spec["rank_by"]),
            "followers": [
                f for f, fs in column_config.items() if fs.get("tether") == name
            ],
        }
        for name, spec in column_config.items()
        if "rank_by" in spec and "tether" not in spec
    }


def _resolve_columns(
    group: pd.DataFrame, column_config: dict, leads: dict, site_order: list[str]
) -> dict:
    """Resolve all non-drop columns for a single primary-key group.

    Each lead column takes the value of its winning row, and the columns
    tethered to it take theirs from the same row; with no winner (every
    value empty), they are all empty. Returns {column_name: value}.
    """
    results = {}
    for lead, rule in leads.items():
        winner = _winning_row(group, lead, rule["criteria"], site_order)
        for column in (lead, *rule["followers"]):
            if not column_config[column].get("drop", False):
                results[column] = "" if winner is None else group.loc[winner, column]
    return results


def merge_table(
    df: pd.DataFrame, table_config: dict, site_order: list[str], id_column: str
) -> pd.DataFrame:
    """Merge a single table's concatenated data.

    The data and config have been checked by ``check_table``.

    Parameters
    ----------
    df : DataFrame
        Every site's rows, with __group_id__ and __site__ columns.
    table_config : dict
        The table's merge rules, from the bundle.
    site_order : list[str]
        The sites, in tie-breaking order.
    id_column : str
        The id column of the primary key, whose values are __group_id__.

    Returns
    -------
    DataFrame
        Merged output.
    """
    primary_key = table_config["primary_key"]
    partition_by = table_config.get("partition_by")
    if not partition_by:
        column_config = table_config.get("column", {})
        return _merge_simple_table(
            df, column_config, primary_key, site_order, id_column
        )

    partition_results = []
    for partition_value, partition_spec in table_config["partitions"].items():
        partition_df = df[df[partition_by] == partition_value]
        if len(partition_df) == 0:
            continue
        column_config = partition_spec.get("column", {})
        partition_results.append(
            _merge_simple_table(
                partition_df, column_config, primary_key, site_order, id_column
            )
        )
    if not partition_results:
        return pd.DataFrame()
    return pd.concat(partition_results, ignore_index=True)


def _merge_simple_table(
    df: pd.DataFrame,
    column_config: dict,
    primary_key: list[str],
    site_order: list[str],
    id_column: str,
) -> pd.DataFrame:
    """Merge a table (or one partition of it) with one set of column rules."""
    group_cols = ["__group_id__" if c == id_column else c for c in primary_key]

    if not column_config:
        result = df[group_cols].drop_duplicates().copy()
        return result.rename(columns={"__group_id__": id_column})

    output_columns = list(primary_key) + [
        name for name, spec in column_config.items() if not spec.get("drop", False)
    ]
    leads = _lead_rules(column_config)
    merged_rows = []
    for group_key, group_df in df.groupby(group_cols, sort=False):
        keys = group_key if isinstance(group_key, tuple) else (group_key,)
        row = dict(zip(group_cols, keys, strict=True))
        row.update(_resolve_columns(group_df, column_config, leads, site_order))
        merged_rows.append(row)

    result = pd.DataFrame(merged_rows)
    result = result.rename(columns={"__group_id__": id_column})
    return result[[c for c in output_columns if c in result.columns]]
