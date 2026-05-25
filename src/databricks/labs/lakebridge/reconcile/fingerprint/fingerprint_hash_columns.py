"""Resolve which columns participate in fingerprint detection.

Mirrors HashQueryBuilder.build_query column resolution exactly so the fingerprint
hashes the same set, in the same order, the row-hash path would. The two
implementations are pinned together by regression tests, but the duplication is
a known maintenance hazard — extracting a shared ``compute_hash_columns`` helper
into ``query_builder/`` is deferred until the second source dialect lands and
informs the right abstraction boundary (forcing the shape on Redshift alone
risks over-fitting).
"""

from __future__ import annotations

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.recon_config import Table, Schema


def _strip(name: str) -> str:
    """Return ``name`` lowercased and without ANSI/source delimiters.

    Bridges the two naming conventions: ``Table.join_columns`` are bare,
    ``Schema.column_name`` (and ``get_select_columns`` output) are ANSI-delimited.
    Set operations on the two would otherwise produce duplicate entries.
    """
    return DialectUtils.unnormalize_identifier(name).lower()


def hash_columns_ordered_for_reconcile(
    table_conf: Table,
    schema: list[Schema],
    layer: str,
    data_source: DataSource,
) -> list[str]:
    """Mirror HashQueryBuilder hash column set + sort order (case-insensitive sort_key)."""
    join_keys = {_strip(c): c for c in (table_conf.join_columns or [])}
    select_keys: dict[str, str] = {}
    for col_name in table_conf.get_select_columns(schema, layer):
        select_keys.setdefault(_strip(col_name), col_name)
    threshold_keys = {_strip(c) for c in table_conf.get_threshold_columns(layer)}
    drop_keys = {_strip(c) for c in table_conf.get_drop_columns(layer)}

    merged: dict[str, str] = {}
    for stripped, original in {**select_keys, **join_keys}.items():
        if stripped in threshold_keys or stripped in drop_keys:
            continue
        merged[stripped] = original

    hash_cols_with_sort = []
    for original in merged.values():
        sort_key = DialectUtils.unnormalize_identifier(
            data_source.normalize_identifier(original).ansi_normalized
        ).lower()
        hash_cols_with_sort.append((sort_key, original))
    return [c for _, c in sorted(hash_cols_with_sort, key=lambda x: x[0])]
