"""Public entry points for recon config auto-discovery.

Two pure functions over a `TableRecon`, each saves the result to the install folder:

- `discover_tables(...)` — list source/target schemas, return matched table pairs.
- `auto_configure_tables(table_recon, ...)` — apply every registered
  `TableAutoConfigurer` to each Table in the input `table_recon`.

A `auto_configure_table(table, ...)` helper applies the same configurers to a
single Table without touching the file — useful for spot-fixing one row.

CLI operation names map directly:

- `discover-tables`                  → `discover_tables(...)`
- `auto-configure-tables`            → load file → `auto_configure_tables(loaded, ...)`
- `discover-auto-configure-tables`   → `discover_tables(...)` → `auto_configure_tables(discovered, ...)`

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
    AutoConfigureContext,
    ColumnMappingAutoConfigurer,
    TableAutoConfigurer,
    TableMatcher,
)
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.recon_config import Table
from databricks.labs.lakebridge.reconcile.utils import initialise_data_source

logger = logging.getLogger(__name__)


# Register auto-configurers here. Order is the order they run for each Table.
SUPPORTED_AUTO_CONFIGURERS: tuple[TableAutoConfigurer, ...] = (ColumnMappingAutoConfigurer(),)


def discover_tables(
    *,
    reconcile_config: ReconcileConfig,
    spark: SparkSession,
    installation: Installation | None = None,
) -> TableRecon:
    """Discover source/target table pairs. If `installation` is provided, also save the draft."""
    source, target = _build_adapters(reconcile_config, spark)
    src = reconcile_config.source
    tgt = reconcile_config.target
    table_recon = TableMatcher().discover(
        source=source,
        source_catalog=src.catalog,
        source_schema=src.schema,
        target=target,
        target_catalog=tgt.catalog,
        target_schema=tgt.schema,
    )
    if installation is not None:
        _save(installation, reconcile_config.table_recon_filename, table_recon)
    return table_recon


def auto_configure_tables(
    table_recon: TableRecon,
    *,
    reconcile_config: ReconcileConfig,
    spark: SparkSession,
    installation: Installation | None = None,
    auto_configurers: Sequence[TableAutoConfigurer] = SUPPORTED_AUTO_CONFIGURERS,
) -> TableRecon:
    """Apply `auto_configurers` to each Table in `table_recon`. Defaults to `SUPPORTED_AUTO_CONFIGURERS`.

    Pass a custom `auto_configurers` list to inject your own `TableAutoConfigurer`
    implementations (e.g. an LLM-driven mapper) alongside or in place of the defaults.
    If `installation` is provided, also save the result.
    """
    source, target = _build_adapters(reconcile_config, spark)
    src = reconcile_config.source
    tgt = reconcile_config.target
    configured = [
        _auto_configure_one(
            t,
            configurers=auto_configurers,
            source=source,
            source_catalog=src.catalog,
            source_schema=src.schema,
            target=target,
            target_catalog=tgt.catalog,
            target_schema=tgt.schema,
        )
        for t in table_recon.tables
    ]
    result = TableRecon(tables=configured)
    if installation is not None:
        _save(installation, reconcile_config.table_recon_filename, result)
    return result


def auto_configure_table(
    *,
    table: Table,
    reconcile_config: ReconcileConfig,
    spark: SparkSession,
    auto_configurers: Sequence[TableAutoConfigurer] = SUPPORTED_AUTO_CONFIGURERS,
) -> Table:
    """Apply `auto_configurers` to a single Table. Defaults to `SUPPORTED_AUTO_CONFIGURERS`. No file upload."""
    source, target = _build_adapters(reconcile_config, spark)
    src = reconcile_config.source
    tgt = reconcile_config.target
    return _auto_configure_one(
        table,
        configurers=auto_configurers,
        source=source,
        source_catalog=src.catalog,
        source_schema=src.schema,
        target=target,
        target_catalog=tgt.catalog,
        target_schema=tgt.schema,
    )


def _auto_configure_one(
    table: Table,
    *,
    configurers: Sequence[TableAutoConfigurer],
    source: DataSource,
    source_catalog: str,
    source_schema: str,
    target: DataSource,
    target_catalog: str,
    target_schema: str,
) -> Table:
    ctx = AutoConfigureContext(
        source=source,
        source_catalog=source_catalog,
        source_schema=source_schema,
        source_columns=source.get_schema(source_catalog, source_schema, table.source_name),
        target=target,
        target_catalog=target_catalog,
        target_schema=target_schema,
        target_columns=target.get_schema(target_catalog, target_schema, table.target_name),
    )
    for configurer in configurers:
        table = configurer.configure(table, ctx)
    return table


def _save(installation: Installation, filename: str, table_recon: TableRecon) -> None:
    installation.upload(filename, json.dumps(asdict(table_recon), indent=2).encode())
    logger.info(f"Saved table mappings to {filename} ({len(table_recon.tables)} table(s))")


def _build_adapters(reconcile_config: ReconcileConfig, spark: SparkSession) -> tuple[DataSource, DataSource]:
    return initialise_data_source(spark, reconcile_config.source.dialect, reconcile_config.source.uc_connection_name)
