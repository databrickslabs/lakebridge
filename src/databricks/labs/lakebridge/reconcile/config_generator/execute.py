"""Orchestrator + workspace-job entry point for recon config auto-discovery.

How it works
------------
Two-stage pipeline per Table:

1. Discover table pairs from source/target schemas via `TableMatcher`.
2. For each discovered Table, apply every registered `TableAutoConfigurer` in
   declared order (outer loop = tables, inner loop = configurers).

The CLI dispatches to one of three operation names depending on the user's
answers to "discover?" and "auto-configure?":

- `discover-tables`             → discover only, `auto_configurers=[]`
- `discover-auto-configure-tables`   → discover + apply all configurers
- `auto-configure-tables`            → load existing TableRecon, apply all configurers (no re-discover)

How to extend
-------------
Add a new auto-configurer:

1. Write a class implementing `TableAutoConfigurer` (see `ColumnMappingAutoConfigurer`).
2. Append an instance to `SUPPORTED_AUTO_CONFIGURERS` below.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from collections.abc import Sequence

from pyspark.sql import SparkSession
from databricks.labs.blueprint.installation import Installation

from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon
from databricks.labs.lakebridge.reconcile.config_generator.configure import (
    ColumnMappingAutoConfigurer,
    TableAutoConfigurer,
    TableMatcher,
)
from databricks.labs.lakebridge.reconcile.connectors.source_adapter import create_adapter
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.recon_config import Table
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

logger = logging.getLogger(__name__)


# Register auto-configurers here. Order is the order they run for each Table.
SUPPORTED_AUTO_CONFIGURERS: Sequence[TableAutoConfigurer] = [
    ColumnMappingAutoConfigurer(),
]


def auto_configure_tables(
    *,
    installation: Installation,
    table_recon_filename: str,
    source: DataSource,
    source_catalog: str,
    source_schema: str,
    target: DataSource,
    target_catalog: str,
    target_schema: str,
    auto_configurers: Sequence[TableAutoConfigurer],
    table_recon: TableRecon | None = None,
) -> TableRecon:
    """Discover (or reuse) table pairs, apply each configurer to every Table, save the file."""
    table_recon = table_recon or TableMatcher().discover(
        source=source,
        source_catalog=source_catalog,
        source_schema=source_schema,
        target=target,
        target_catalog=target_catalog,
        target_schema=target_schema,
    )

    configured = [
        _apply_configurers(
            t,
            auto_configurers,
            source=source,
            source_catalog=source_catalog,
            source_schema=source_schema,
            target=target,
            target_catalog=target_catalog,
            target_schema=target_schema,
        )
        for t in table_recon.tables
    ]
    table_recon = TableRecon(tables=configured)

    installation.upload(table_recon_filename, json.dumps(asdict(table_recon), indent=2).encode())
    logger.info(f"Saved table mappings to {table_recon_filename} ({len(table_recon.tables)} table(s))")
    return table_recon


def _apply_configurers(
    table: Table,
    configurers: Sequence[TableAutoConfigurer],
    *,
    source: DataSource,
    source_catalog: str,
    source_schema: str,
    target: DataSource,
    target_catalog: str,
    target_schema: str,
) -> Table:
    for configurer in configurers:
        table = configurer.configure(
            table=table,
            source=source,
            source_catalog=source_catalog,
            source_schema=source_schema,
            target=target,
            target_catalog=target_catalog,
            target_schema=target_schema,
        )
    return table


def build_adapters(reconcile_config: ReconcileConfig, spark: SparkSession) -> tuple[DataSource, DataSource]:
    """Build (source, target) DataSource adapters from reconcile_config. Used by the workspace-job entry point."""
    src = reconcile_config.source
    source_ds = create_adapter(
        engine=get_dialect(src.dialect),
        spark=spark,
        connection_name=src.uc_connection_name or "",
    )
    target_ds = create_adapter(
        engine=get_dialect("databricks"),
        spark=spark,
        connection_name="",
    )
    return source_ds, target_ds
