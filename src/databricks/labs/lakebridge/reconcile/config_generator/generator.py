from __future__ import annotations

import logging

from databricks.labs.lakebridge.config import TableRecon
from databricks.labs.lakebridge.reconcile.config_generator.matchers import (
    NormalizedMatcher,
    ReconcileStrategy,
    run_strategy_chain,
)
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.recon_config import ColumnMapping, Table

logger = logging.getLogger(__name__)


def generate_table_recon(
    *,
    source: DataSource,
    source_catalog: str,
    source_schema: str,
    target: DataSource,
    target_catalog: str,
    target_schema: str,
    strategies: list[ReconcileStrategy] | None = None,
) -> TableRecon:
    """Generate a draft `TableRecon` by discovering and matching tables/columns.

    Tables are matched by name across the source and target schemas using the
    given strategy chain (defaults to `NormalizedMatcher`). For each matched
    pair, columns are also matched by name and emitted as `ColumnMapping`
    entries when names differ. Unmatched source tables are omitted from the
    draft and logged for the user to add manually.
    """
    strategies = strategies or [NormalizedMatcher()]

    source_tables = source.list_tables(source_catalog, source_schema)
    target_tables = target.list_tables(target_catalog, target_schema)

    table_name_mapping = run_strategy_chain(strategies, source_tables, target_tables)

    tables: list[Table] = []
    unmatched: list[str] = []
    for src_table in source_tables:
        tgt_table = table_name_mapping[src_table]
        if tgt_table is None:
            unmatched.append(src_table)
            continue
        column_mapping = _build_column_mapping(
            source=source,
            source_catalog=source_catalog,
            source_schema=source_schema,
            source_table=src_table,
            target=target,
            target_catalog=target_catalog,
            target_schema=target_schema,
            target_table=tgt_table,
            strategies=strategies,
        )
        tables.append(
            Table(
                source_name=src_table,
                target_name=tgt_table,
                column_mapping=column_mapping or None,
            )
        )

    if unmatched:
        logger.warning(
            "Could not auto-match %d source table(s); add manually to the draft: %s",
            len(unmatched),
            ", ".join(unmatched),
        )

    return TableRecon(tables=tables)


def _build_column_mapping(
    *,
    source: DataSource,
    source_catalog: str,
    source_schema: str,
    source_table: str,
    target: DataSource,
    target_catalog: str,
    target_schema: str,
    target_table: str,
    strategies: list[ReconcileStrategy],
) -> list[ColumnMapping]:
    source_columns = source.get_schema(source_catalog, source_schema, source_table)
    target_columns = target.get_schema(target_catalog, target_schema, target_table)

    source_names = [c.column_name for c in source_columns]
    target_names = [c.column_name for c in target_columns]

    name_mapping = run_strategy_chain(strategies, source_names, target_names)

    mappings: list[ColumnMapping] = []
    unmatched: list[str] = []
    for src_col in source_names:
        tgt_col = name_mapping[src_col]
        if tgt_col is None:
            unmatched.append(src_col)
            continue
        if src_col != tgt_col:
            mappings.append(ColumnMapping(source_name=src_col, target_name=tgt_col))

    if unmatched:
        logger.warning(
            "Could not auto-match %d column(s) for %s -> %s: %s",
            len(unmatched),
            source_table,
            target_table,
            ", ".join(unmatched),
        )

    return mappings
