"""ClickHouse metadata extract step for the Lakebridge profiler.

Runs the read-only ``system.*`` collectors and writes each named result set as its own DuckDB table
(``<collector>_<result_set>``), mirroring the BigQuery extractor's one-table-per-analysis
convention. The pipeline invokes this file
by path: ``python ch_metadata_extract.py --db-path <db> --credential-config-path <creds.yml>``; the
final stdout line is the structured status payload the pipeline parses.

Cloud vs OSS query behaviour is chosen inside the collectors from the live ``cloud_mode`` probe /
``*.clickhouse.cloud`` host, so the same script backs both the ``oss`` and ``cloud`` pipeline
variants. Redaction of sensitive fields (auth params, host IPs, row-policy filters, SQL text) is on
by default and controlled by the ``redact`` credential.
"""

import importlib.resources as pkg_resources
import json
import sys
from datetime import datetime, timezone
from typing import Any

import duckdb
import pandas as pd

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.connections.credential_manager import CredentialManager, create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.resources.assessments.common.cli import arguments_loader
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb
from databricks.labs.lakebridge.resources.assessments.clickhouse import CLICKHOUSE_CLOUD_HOST_SUFFIX
from databricks.labs.lakebridge.resources.assessments.clickhouse.connection import ClickHouseConnection
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.base import (
    ProfilerJSONEncoder,
    is_missing_object_error,
    redact_structure,
    redact_value,
)
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.workload import WorkloadCollector
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.objects import ObjectsCollector
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.features import FeaturesCollector
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.dependencies import DependenciesCollector
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.utilization import UtilizationCollector
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.security import SecurityCollector
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.costs import CostsCollector
from databricks.labs.blueprint.entrypoint import get_logger

logger = get_logger(__file__)

_CLICKHOUSE_RESOURCES = "databricks.labs.lakebridge.resources.assessments.clickhouse"


def detect_cloud(conn: ClickHouseConnection, config: dict[str, Any]) -> bool:
    """Return True for ClickHouse Cloud, False for self-managed (OSS).

    A ``*.clickhouse.cloud`` host is a definitive yes; otherwise the ``cloud_mode`` server setting
    decides (``1`` on Cloud; absent/``0`` on OSS). Same rule as ``variants.resolve_clickhouse_variant``.
    """
    host = str(config.get("host") or "").strip().lower()
    if host.endswith(CLICKHOUSE_CLOUD_HOST_SUFFIX):
        return True
    try:
        rows = conn.query("SELECT value FROM system.settings WHERE name = 'cloud_mode'")
    except Exception as e:
        # A missing setting degrades to OSS; a real connection/permission error must fail loudly
        # rather than be mis-typed as OSS and run the extract against the wrong variant.
        if is_missing_object_error(str(e)):
            return False
        raise
    return bool(rows) and str(rows[0].get("value", "")).strip().lower() in {"1", "true"}


def _reset_duckdb(db_path: str) -> None:
    """Drop every existing table in the output DuckDB so this run's tables are created fresh.

    The extract owns the file exclusively, and some tables change shape by variant, so overwriting
    in place (TRUNCATE + INSERT) against a prior run's schema is unsafe.
    """
    with duckdb.connect(db_path) as conn:
        for (table_name,) in conn.execute("SHOW TABLES").fetchall():
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')


def _load_table_schemas() -> dict[str, list[list[str]]]:
    """Load the declared ``<collector>_<result_set>`` -> [[col, duckdb_type], ...] catalog.

    Used to create empty-but-typed stub tables when a result set has no rows, so every table always
    exists (consistent with the mssql *_ddl.sql and BigQuery analysis_types.json profilers). A
    populated result set overwrites the stub with ClickHouse's own inferred types.
    """
    text = (pkg_resources.files(_CLICKHOUSE_RESOURCES) / "table_schemas.json").read_text(encoding="utf-8")
    return json.loads(text)


# Collector classes run in order. Each returns a dict of named result sets; every result set becomes
# one DuckDB table named "<collector>_<result_set>".
COLLECTORS = (
    WorkloadCollector,
    ObjectsCollector,
    FeaturesCollector,
    DependenciesCollector,
    UtilizationCollector,
    SecurityCollector,
    CostsCollector,
)


def _rows_to_dataframe(rows: list[dict[str, Any]], redact: bool) -> pd.DataFrame:
    """Flatten a collector result set (list of row dicts) into a DataFrame.

    Sensitive string fields are replaced with ``[REDACTED]`` when ``redact`` is on. Nested values
    (lists/dicts from ClickHouse array/tuple columns) are JSON-encoded so they land in a single
    DuckDB column rather than exploding the schema.
    """
    normalized: list[dict[str, Any]] = []
    for row in rows:
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if redact:
                value = redact_value(key, value)
                # Recurse into struct/map columns so a nested sensitive field is caught, not just the column.
                if isinstance(value, (list, dict)):
                    value = redact_structure(value)
            if isinstance(value, (list, dict)):
                value = json.dumps(value, cls=ProfilerJSONEncoder)
            clean[key] = value
        normalized.append(clean)
    return pd.DataFrame(normalized)


def _dict_to_dataframe(payload: dict[str, Any], redact: bool) -> pd.DataFrame:
    """Flatten a single-object result set (e.g. storage_total, pricing_config) into a one-row frame."""
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if redact:
            value = redact_value(key, value)
        if isinstance(value, (list, dict)):
            value = json.dumps(value, cls=ProfilerJSONEncoder)
        clean[key] = value
    return pd.DataFrame([clean]) if clean else pd.DataFrame()


def _result_set_to_dataframe(result_set: Any, redact: bool) -> pd.DataFrame:
    """Coerce any collector result set (list of rows, or a single dict) into a DataFrame."""
    if isinstance(result_set, list):
        return _rows_to_dataframe(result_set, redact)
    if isinstance(result_set, dict):
        return _dict_to_dataframe(result_set, redact)
    # Scalar / unexpected shape: wrap it so nothing is silently dropped.
    return pd.DataFrame([{"value": result_set}])


def _duckdb_schema(columns: list[list[str]]) -> str | None:
    """Build a DuckDB ``CREATE TABLE`` column list ('"name" TYPE, ...') from a catalog entry.

    Column names are double-quoted because several are DuckDB reserved words (``table``, ``column``,
    ``type``). Returns None when no columns are declared, so the caller skips a table it cannot create
    rather than emitting invalid DDL.
    """
    if not columns:
        return None
    return ", ".join(f'"{name}" {dtype}' for name, dtype in columns)


def execute(credential_manager: CredentialManager, db_path: str) -> None:
    ch_settings = credential_manager.get_credentials("clickhouse")

    # Profiler knobs (days_back, redact) are nested under "profiler"; connection keys are flat.
    profiler_cfg: dict[str, Any] = ch_settings.get("profiler", {}) or {}
    config: dict[str, Any] = dict(ch_settings)
    # Coerce days_back the same way BaseCollector does (falls back to 30 on a non-numeric value)
    # rather than aborting the whole extract on a bad credential; BaseCollector re-coerces per
    # collector, so this stays consistent end-to-end.
    try:
        config["days_back"] = int(profiler_cfg.get("days_back", 30))
    except (TypeError, ValueError):
        config["days_back"] = 30
    # Redaction defaults ON — strip auth params, host IPs, row-policy filters, and SQL text.
    redact = bool(profiler_cfg.get("redact", True))

    table_schemas = _load_table_schemas()
    # Drop any tables left by a prior run into the same output DuckDB. The extract owns this file
    # entirely (single python step), and some tables change shape by variant (e.g. costs_pricing_config
    # is 8 columns on Cloud, 4 on OSS). save_to_duckdb's overwrite TRUNCATEs an existing table in place,
    # so without this a Cloud-then-OSS re-run into the same folder would insert into a stale schema.
    _reset_duckdb(db_path)
    conn = ClickHouseConnection(config)
    try:
        conn.connect()
        server_version = conn.server_version()
        # Detect Cloud once (shared by every collector via config["is_cloud"]) so per-node log tables
        # are read across replicas with clusterAllReplicas(). Same rule as variants.resolve_clickhouse_variant.
        config["is_cloud"] = detect_cloud(conn, config)
        if config["is_cloud"]:
            conn.enable_cluster_reads()
        logger.info(
            f"Connected to ClickHouse {server_version}; is_cloud={config['is_cloud']}, "
            f"days_back={config['days_back']}, redact={redact}"
        )

        row_counts: dict[str, int] = {}
        errors: list[str] = []
        for collector_cls in COLLECTORS:
            collector = collector_cls(conn, config)
            logger.info(f"Collecting: {collector.name} ({collector.description})")
            results = collector.collect()
            errors.extend(collector.errors)
            for result_name, result_set in results.items():
                table_name = f"{collector.name}_{result_name}"
                df = _result_set_to_dataframe(result_set, redact)
                if df.shape[1] == 0:
                    # Empty result set -> no columns from the driver (ClickHouse-over-HTTP omits column
                    # metadata for a 0-row result). Create the table empty-but-typed from the declared
                    # schema catalog so every table always exists (consistent with the mssql/BigQuery
                    # profilers). A populated run overwrites it with ClickHouse's own types.
                    declared = table_schemas.get(table_name, [])
                    if not declared:
                        # No catalog entry -> nothing to declare; skip rather than emit invalid DDL.
                        row_counts[table_name] = 0
                        logger.info(f"  skipped empty table {table_name} (no schema in catalog)")
                        continue
                    # A column-named empty frame so save_to_duckdb can register it; the schema pins types.
                    empty_df = pd.DataFrame(columns=[col for col, _ in declared])
                    save_to_duckdb(empty_df, table_name, db_path, schema=_duckdb_schema(declared))
                    row_counts[table_name] = 0
                    logger.info(f"  wrote empty table {table_name} (0 rows, schema from catalog)")
                    continue
                save_to_duckdb(df, table_name, db_path)
                row_counts[table_name] = len(df)
                logger.info(f"  wrote table {table_name}: {len(df)} rows")
    finally:
        conn.close()

    # Final stdout line is the structured payload the pipeline parses to decide success/error.
    print(
        json.dumps(
            {
                "status": "success",
                "message": "ClickHouse metadata extract complete",
                "tables": sorted(row_counts.keys()),
                "rows": row_counts,
                "warnings": errors,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )


if __name__ == "__main__":
    initialize_logging()
    _db_path, _creds_file = arguments_loader(desc="ClickHouse Metadata Extract Script")
    try:
        execute(
            credential_manager=create_credential_manager(PRODUCT_NAME, EnvGetter(), creds_path=_creds_file),
            db_path=_db_path,
        )
    except Exception as exc:  # top-level: emit a structured error payload the pipeline can parse
        logger.error(f"ClickHouse metadata extract failed: {exc}")
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        sys.exit(1)
