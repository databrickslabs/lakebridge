# pylint: disable=invalid-name
import logging

from databricks.labs.blueprint.installation import Installation
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.deployment.upgrade_common import (
    installed_table_columns,
    table_original_query,
)
from databricks.labs.lakebridge.helpers import db_sql

logger = logging.getLogger(__name__)


def _details_backfill_sql(target: str, legacy: str) -> str:
    """Explode the legacy ARRAY<MAP> details rows into one record-level row each.

    A mismatch record's map carries the join keys plus a ``<col>_base`` / ``<col>_compare`` /
    ``<col>_match`` triple per compared column; split those onto record_key / source_row /
    target_row / mismatch_columns. Every other recon_type's map is a whole row image, kept on the
    side it came from (target for ``missing_in_source``, source otherwise). Schema-comparison rows
    (recon_type = 'schema') are handled by :func:`_schema_details_backfill_sql`. Values are strings
    because the legacy column was MAP<STRING, STRING>.
    """
    return f"""INSERT INTO {target}
SELECT
    recon_table_id,
    recon_type,
    CASE
        WHEN recon_type = 'mismatch'
        THEN parse_json(to_json(map_filter(rec, (k, v) -> NOT (endswith(k, '_base') OR endswith(k, '_compare') OR endswith(k, '_match')))))
        ELSE parse_json(to_json(rec))
    END,
    CASE
        WHEN recon_type = 'mismatch'
        THEN parse_json(to_json(map_from_entries(transform(filter(map_keys(rec), k -> endswith(k, '_base')), k -> struct(left(k, length(k) - 5), rec[k])))))
        WHEN recon_type = 'missing_in_source' THEN CAST(NULL AS VARIANT)
        ELSE parse_json(to_json(rec))
    END,
    CASE
        WHEN recon_type = 'mismatch'
        THEN parse_json(to_json(map_from_entries(transform(filter(map_keys(rec), k -> endswith(k, '_compare')), k -> struct(left(k, length(k) - 8), rec[k])))))
        WHEN recon_type = 'missing_in_source' THEN parse_json(to_json(rec))
        ELSE CAST(NULL AS VARIANT)
    END,
    CASE
        WHEN recon_type = 'mismatch'
        THEN transform(filter(map_keys(rec), k -> endswith(k, '_match') AND rec[k] = 'false'), k -> left(k, length(k) - 6))
        ELSE CAST(NULL AS ARRAY<STRING>)
    END,
    inserted_ts
FROM (SELECT recon_table_id, recon_type, explode(data) AS rec, inserted_ts FROM {legacy} WHERE recon_type <> 'schema')"""


def _schema_details_backfill_sql(target: str, legacy: str) -> str:
    """Move legacy schema-comparison rows (recon_type = 'schema') into the typed schema_details table."""
    return f"""INSERT INTO {target}
SELECT recon_table_id, rec['source_column'], rec['source_datatype'], rec['databricks_column'],
       rec['databricks_datatype'], CAST(rec['is_valid'] AS BOOLEAN), inserted_ts
FROM (SELECT recon_table_id, explode(data) AS rec, inserted_ts FROM {legacy} WHERE recon_type = 'schema')"""


def _aggregate_details_backfill_sql(target: str, legacy: str) -> str:
    """Aggregate detail rows keep the whole aggregated row as a VARIANT image on the source side."""
    return f"""INSERT INTO {target}
SELECT recon_table_id, rule_id, recon_type, parse_json(to_json(rec)), parse_json(to_json(rec)),
       CAST(NULL AS VARIANT), CAST(NULL AS ARRAY<STRING>), inserted_ts
FROM (SELECT recon_table_id, rule_id, recon_type, explode(data) AS rec, inserted_ts FROM {legacy})"""


def _migrate_details_table(ws: WorkspaceClient, prefix: str) -> bool:
    """Migrate the row-level details table; schema rows are split out into schema_details."""
    identifier = f"{prefix}.details"
    if "data" not in installed_table_columns(ws, identifier):
        logger.info(f"{identifier} is already on the record-level schema; nothing to migrate")
        return False
    backend = db_sql.get_sql_backend(ws)
    backup = f"{identifier}_backup"
    schema_details = f"{prefix}.schema_details"
    logger.info(f"Migrating {identifier} to the record-level schema; raw rows preserved in {backup}")
    backend.execute(f"ALTER TABLE {identifier} RENAME TO {backup}")
    backend.execute(table_original_query("details", identifier))
    backend.execute(table_original_query("schema_details", schema_details))
    backend.execute(_details_backfill_sql(identifier, backup))
    backend.execute(_schema_details_backfill_sql(schema_details, backup))
    return True


def _migrate_aggregate_details_table(ws: WorkspaceClient, prefix: str) -> bool:
    identifier = f"{prefix}.aggregate_details"
    if "data" not in installed_table_columns(ws, identifier):
        logger.info(f"{identifier} is already on the record-level schema; nothing to migrate")
        return False
    backend = db_sql.get_sql_backend(ws)
    backup = f"{identifier}_backup"
    logger.info(f"Migrating {identifier} to the record-level schema; raw rows preserved in {backup}")
    backend.execute(f"ALTER TABLE {identifier} RENAME TO {backup}")
    backend.execute(table_original_query("aggregate_details", identifier))
    backend.execute(_aggregate_details_backfill_sql(identifier, backup))
    return True


def upgrade(installation: Installation, ws: WorkspaceClient):
    """Migrate details / aggregate_details from the ARRAY<MAP> model to the record-level VARIANT model.

    Old installs created these tables with a single ``data ARRAY<MAP<STRING, STRING>>`` column;
    CREATE TABLE IF NOT EXISTS would otherwise leave the old schema in place and the new reconcile
    writes (and the details_columns view) would fail. The legacy rows are exploded and transformed
    into the new columns; the original tables are kept as ``*_backup`` so nothing is lost.
    """
    app_context = ApplicationContext(ws)
    reconcile_config = app_context.recon_config
    if reconcile_config is None:
        logger.info("No reconcile configuration found; skipping recon details migration")
        return
    prefix = f"{reconcile_config.metadata_config.catalog}.{reconcile_config.metadata_config.schema}"
    migrated = _migrate_details_table(ws, prefix)
    migrated = _migrate_aggregate_details_table(ws, prefix) or migrated
    if migrated:
        installation.save(reconcile_config)
        logger.info("Reconcile details/aggregate_details migrated to the record-level VARIANT schema")
