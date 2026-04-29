from __future__ import annotations

import json
import logging
from dataclasses import asdict

from pyspark.sql import SparkSession
from databricks.connect import DatabricksSession
from databricks.labs.blueprint.installation import Installation
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon
from databricks.labs.lakebridge.reconcile.config_generator.generator import generate_table_recon
from databricks.labs.lakebridge.reconcile.connectors.source_adapter import create_adapter
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

logger = logging.getLogger(__name__)


def configure_tables(
    *,
    ws: WorkspaceClient,
    installation: Installation,
    reconcile_config: ReconcileConfig,
    spark: SparkSession | None = None,
) -> TableRecon:
    """Discover source/target tables, generate a draft `TableRecon`, and save it.

    Runs inside the reconcile job (where Spark + foreign-connection access are
    available). The draft is saved under the canonical filename used by the
    reconcile runtime, so a subsequent `databricks labs lakebridge reconcile`
    will pick it up. The user edits the draft to fill in joins, missing column
    mappings, and any tables that could not be auto-matched.
    """
    spark = spark or DatabricksSession.builder.getOrCreate()

    src = reconcile_config.source
    tgt = reconcile_config.target

    source_ds = create_adapter(
        engine=get_dialect(src.dialect),
        spark=spark,
        ws=ws,
        connection_name=src.uc_connection_name or "",
    )
    target_ds = create_adapter(
        engine=get_dialect("databricks"),
        spark=spark,
        ws=ws,
        connection_name="",
    )

    table_recon = generate_table_recon(
        source=source_ds,
        source_catalog=src.catalog,
        source_schema=src.schema,
        target=target_ds,
        target_catalog=tgt.catalog,
        target_schema=tgt.schema,
    )

    filename = draft_filename(reconcile_config)
    installation.upload(filename, json.dumps(asdict(table_recon), indent=2).encode())
    logger.info(f"Saved draft table mappings to {filename} ({len(table_recon.tables)} table(s))")
    return table_recon


def draft_filename(reconcile_config: ReconcileConfig) -> str:
    """Canonical filename the reconcile runtime loads via `Installation.load`."""
    src = reconcile_config.source
    connection_or_catalog = src.uc_connection_name or src.catalog
    return f"recon_config_{src.dialect}_{connection_or_catalog}_{reconcile_config.report_type}.json"
