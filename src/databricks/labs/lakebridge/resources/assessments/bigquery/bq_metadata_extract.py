"""
Step 1 of the Lakebridge BigQuery profiler pipeline.

Reads BQ profiling SQL templates from package resources, runs each against the customer's
BigQuery project(s) and region(s), and writes the merged results into a local DuckDB file.
The DuckDB file is later uploaded to a UC volume and ingested into Delta tables by the
existing source-tech-agnostic deployment path.

Two-level execution:
  * Serial across (project, region) — each iteration uses its own bigquery.Client (the
    region/location is per-client).
  * Parallel across the 16 SQL files within each iteration via a ThreadPoolExecutor of
    `max_parallel_sqls` workers (default 8). Per-SQL DataFrames accumulate in memory and
    are concat-ed + overwritten to DuckDB once at the end — no partial state on crash,
    re-runs are idempotent.
"""

import importlib.resources as pkg_resources
import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.resources.assessments.bigquery.common.compiler import Compiler
from databricks.labs.lakebridge.resources.assessments.bigquery.common.functions import create_bigquery_client
from databricks.labs.lakebridge.resources.assessments.common.cli import arguments_loader
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb

# Use the canonical dotted name (not `__name__`, which is `"__main__"` when this script is
# invoked as the entrypoint by pipeline.py or `python -m ...`). That keeps the logger inside
# the `databricks.*` namespace that `initialize_logging()` sets to INFO.
logger = logging.getLogger("databricks.labs.lakebridge.resources.assessments.bigquery.bq_metadata_extract")

# Logical analysis_type → list of SQL files that feed it. Derived 1:1 from analysis_types.json
# except for consumption_{beyond,through}_commitments which fan in 3 variant files each.
_SQL_FILE_TO_ANALYSIS_TYPE: dict[str, str] = {
    "fulfillment_analysis.sql": "fulfillment_analysis",
    "table_storage.sql": "table_storage",
    "timeline_analysis.sql": "timeline_analysis",
    "workload_types.sql": "workload_types",
    "commitment_changes.sql": "commitment_changes",
    "commitments.sql": "commitments",
    "jobs_timeline_by_reservations.sql": "jobs_timeline_by_reservations",
    "reservation_timeline_analysis.sql": "reservation_timeline_analysis",
    "streaming_summary.sql": "streaming_summary",
    "write_api_summary.sql": "write_api_summary",
    "consumption_beyond_commitments_standard.sql": "consumption_beyond_commitments",
    "consumption_beyond_commitments_enterprise.sql": "consumption_beyond_commitments",
    "consumption_beyond_commitments_enterprise_plus.sql": "consumption_beyond_commitments",
    "consumption_through_commitments_standard.sql": "consumption_through_commitments",
    "consumption_through_commitments_enterprise.sql": "consumption_through_commitments",
    "consumption_through_commitments_enterprise_plus.sql": "consumption_through_commitments",
}

_RESERVATION_FILES = frozenset(
    {
        "commitments.sql",
        "commitment_changes.sql",
        "consumption_beyond_commitments_standard.sql",
        "consumption_beyond_commitments_enterprise.sql",
        "consumption_beyond_commitments_enterprise_plus.sql",
        "consumption_through_commitments_standard.sql",
        "consumption_through_commitments_enterprise.sql",
        "consumption_through_commitments_enterprise_plus.sql",
        "jobs_timeline_by_reservations.sql",
        "reservation_timeline_analysis.sql",
    }
)

_STREAMING_FILES = frozenset({"streaming_summary.sql", "write_api_summary.sql"})

_BIGQUERY_RESOURCES = "databricks.labs.lakebridge.resources.assessments.bigquery"


def _load_resource_text(subpackage: str, filename: str) -> str:
    return pkg_resources.files(f"{_BIGQUERY_RESOURCES}.{subpackage}").joinpath(filename).read_text(encoding="utf-8")


def _load_reference_csv(filename: str) -> pd.DataFrame:
    with pkg_resources.as_file(pkg_resources.files(f"{_BIGQUERY_RESOURCES}.data").joinpath(filename)) as csv_path:
        return pd.read_csv(csv_path)


def _select_sql_files(profiler_cfg: dict[str, Any]) -> list[str]:
    excluded: set[str] = set()
    if profiler_cfg.get("exclude_reservations_data"):
        excluded.update(_RESERVATION_FILES)
    if profiler_cfg.get("exclude_streaming_metrics"):
        excluded.update(_STREAMING_FILES)
    return [f for f in _SQL_FILE_TO_ANALYSIS_TYPE if f not in excluded]


def _run_sql_for_iteration(
    sql_filename: str,
    compiler: Compiler,
    bq_client: Any,
    project_region: str,
) -> tuple[str, pd.DataFrame, float]:
    """Compile + execute one SQL file against the BQ client for a single (project, region) iteration.

    Returns (analysis_type, df, elapsed_seconds). The elapsed time is wall-clock for the BQ
    query + result download, surfaced to the caller for per-SQL progress logging.
    """
    raw_sql = _load_resource_text("sql-client-run", sql_filename)
    compiled_sql = compiler.compile(sql_filename, raw_sql)
    logger.debug(f"Running {sql_filename} for {project_region}")
    start = time.monotonic()
    df = bq_client.query(compiled_sql).to_dataframe()
    elapsed = time.monotonic() - start
    df["source"] = f"{project_region}_{sql_filename}"
    if sql_filename == "table_storage.sql" and "metadatalevel" in df.columns:
        df = df.rename(columns={"metadatalevel": "metadata_level"})
    return _SQL_FILE_TO_ANALYSIS_TYPE[sql_filename], df, elapsed


def _run_iteration(
    project_id: str,
    region: str,
    sql_files: list[str],
    substitutions: list[dict[str, Any]],
    profiling_window_days: int,
    max_parallel_sqls: int,
    accumulators: dict[str, list[pd.DataFrame]],
    accumulator_lock: threading.Lock,
) -> None:
    project_region = f"{project_id}.region-{region}"
    bq_client = create_bigquery_client(project_id, region)
    compiler = Compiler(
        substitutions,
        project_region=project_region,
        profiling_window_in_days=profiling_window_days,
    )

    iter_start = time.monotonic()
    iter_rows = 0
    logger.info(f"[{project_region}] starting {len(sql_files)} SQLs (max_parallel={max_parallel_sqls})")

    with ThreadPoolExecutor(max_workers=max_parallel_sqls) as executor:
        future_to_file = {
            executor.submit(_run_sql_for_iteration, sql_filename, compiler, bq_client, project_region): sql_filename
            for sql_filename in sql_files
        }
        for future in as_completed(future_to_file):
            sql_filename = future_to_file[future]
            try:
                analysis_type, df, elapsed = future.result()
            except (RuntimeError, ValueError, OSError) as exc:
                logger.error(f"SQL '{sql_filename}' failed in {project_region}: {exc}")
                raise
            logger.info(f"[{project_region}]   {sql_filename}: {len(df)} rows in {elapsed:.1f}s")
            iter_rows += len(df)
            with accumulator_lock:
                accumulators[analysis_type].append(df)

    iter_elapsed = time.monotonic() - iter_start
    logger.info(f"[{project_region}] done in {iter_elapsed:.1f}s ({len(sql_files)} SQLs, {iter_rows} rows)")


# BQ schema string types → pandas dtype strings. Used to build empty stub DataFrames so
# downstream dashboards see all 12 tables even when reservations data is excluded — empty
# rows render as empty widgets, missing tables show "TABLE_OR_VIEW_NOT_FOUND" errors.
_BQ_TYPE_TO_PANDAS: dict[str, str] = {
    "string": "object",
    "double": "float64",
    "long": "Int64",
    "integer": "Int64",
    "boolean": "boolean",
    "timestamp": "datetime64[ns, UTC]",
    "date": "datetime64[ns]",
}


def _empty_df_for_analysis_type(analysis_type: str, analysis_types: dict[str, Any]) -> pd.DataFrame:
    spec = analysis_types.get(analysis_type, {})
    fields = spec.get("schema", {}).get("fields", [])
    # Apply the same metadatalevel → metadata_level rename the live extract path applies.
    columns = {}
    for f in fields:
        name = "metadata_level" if f["name"] == "metadatalevel" else f["name"]
        dtype = _BQ_TYPE_TO_PANDAS.get(f["type"].lower(), "object")
        columns[name] = pd.Series(dtype=dtype)
    if columns:
        return pd.DataFrame(columns)
    return pd.DataFrame()


def _write_accumulators(
    accumulators: dict[str, list[pd.DataFrame]],
    db_path: str,
    analysis_types: dict[str, Any],
) -> dict[str, int]:
    """Write every analysis_type as a DuckDB table, using empty stub schemas for any
    analysis_type that wasn't run (e.g. reservations data was excluded). The dashboard
    relies on every table existing — missing tables fail downstream queries."""
    row_counts: dict[str, int] = {}
    for analysis_type, frames in accumulators.items():
        if frames:
            merged = pd.concat(frames, ignore_index=True)
        else:
            logger.info(f"No data accumulated for {analysis_type}; writing empty stub.")
            merged = _empty_df_for_analysis_type(analysis_type, analysis_types)
        save_to_duckdb(merged, analysis_type, db_path)
        row_counts[analysis_type] = len(merged)
    return row_counts


def _write_reference_tables(db_path: str) -> dict[str, int]:
    cluster_df = _load_reference_csv("cluster_pricing.csv")
    save_to_duckdb(cluster_df, "bq_cluster_pricing", db_path)
    sqlwh_df = _load_reference_csv("dbsql_pricing.csv")
    save_to_duckdb(sqlwh_df, "bq_sqlwarehouse_pricing", db_path)
    return {"bq_cluster_pricing": len(cluster_df), "bq_sqlwarehouse_pricing": len(sqlwh_df)}


def execute() -> None:
    db_path, _creds_file = arguments_loader(desc="BigQuery Metadata Extract Script")

    cred_manager = create_credential_manager(PRODUCT_NAME, EnvGetter())
    bq_settings = cred_manager.get_credentials("bigquery")

    pairs: list[dict[str, str]] = bq_settings["pairs"]
    profiling_window_days: int = int(bq_settings.get("profiling_window_days", 180))
    max_parallel_sqls: int = int(bq_settings.get("max_parallel_sqls", 8))
    profiler_cfg: dict[str, Any] = bq_settings.get("profiler", {})

    # `redact_query_text` is stored for future use; none of the 16 PR-1 SQL files emit a
    # query_text column, so it is a no-op today. The flag is wired into the credentials
    # schema now so the v2 object-metadata SQLs can pick it up without a schema change.
    _ = profiler_cfg.get("redact_query_text", True)

    wall_clock_start = time.monotonic()
    try:
        sql_files = _select_sql_files(profiler_cfg)
        if not sql_files:
            raise RuntimeError("All SQL files excluded by config; nothing to extract.")

        substitutions = json.loads(_load_resource_text("common", "substitutions.json"))
        analysis_types = json.loads(_load_resource_text("common", "analysis_types.json"))

        accumulators: dict[str, list[pd.DataFrame]] = {at: [] for at in set(_SQL_FILE_TO_ANALYSIS_TYPE.values())}
        accumulator_lock = threading.Lock()

        for pair in pairs:
            project_id, region = pair["project"], pair["region"]
            logger.info(f"Extracting from project={project_id} region={region}")
            _run_iteration(
                project_id=project_id,
                region=region,
                sql_files=sql_files,
                substitutions=substitutions,
                profiling_window_days=profiling_window_days,
                max_parallel_sqls=max_parallel_sqls,
                accumulators=accumulators,
                accumulator_lock=accumulator_lock,
            )

        row_counts = _write_accumulators(accumulators, db_path, analysis_types)
        row_counts.update(_write_reference_tables(db_path))

        wall_clock_seconds = round(time.monotonic() - wall_clock_start, 2)
        logger.info(f"Total wall-clock: {wall_clock_seconds}s")
        # Final stdout line is the structured payload that pipeline._run_python_script parses
        # to decide success/error. Keep the `print` (matching Synapse's workspace_extract.py
        # convention) — don't replace with logger.info because pipeline reads the last raw line.
        print(
            json.dumps(
                {
                    "status": "success",
                    "tables": sorted(row_counts.keys()),
                    "rows": row_counts,
                    "wall_clock_seconds": wall_clock_seconds,
                }
            )
        )
    # Synapse pattern: catch-all at top level to produce a structured error payload that
    # pipeline.py can parse. Internal failures already propagate with specific types.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(f"BigQuery metadata extract failed: {exc}")
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    initialize_logging()
    execute()
