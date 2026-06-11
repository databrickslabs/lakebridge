import importlib.resources as pkg_resources
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import pandas as pd
from google.cloud import bigquery

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.connections.credential_manager import CredentialManager, create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.resources.assessments.common.sql_substituter import substitute
from databricks.labs.blueprint.entrypoint import get_logger
from databricks.labs.lakebridge.resources.assessments.common.cli import arguments_loader
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb

logger = get_logger(__file__)

# Logical analysis_type → list of SQL files that feed it. Derived 1:1 from analysis_types.json
# except for consumption_{beyond,through}_commitments which fan in 3 variant files each.
SQL_FILE_TO_ANALYSIS_TYPE: dict[str, str] = {
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


def _load_resource_text(filename: str) -> str:
    return (pkg_resources.files(_BIGQUERY_RESOURCES) / "resources" / filename).read_text(encoding="utf-8")


def _select_sql_files(profiler_cfg: dict[str, Any]) -> list[str]:
    excluded: set[str] = set()
    if profiler_cfg.get("exclude_reservations_data"):
        excluded.update(_RESERVATION_FILES)
    if profiler_cfg.get("exclude_streaming_metrics"):
        excluded.update(_STREAMING_FILES)
    return [f for f in SQL_FILE_TO_ANALYSIS_TYPE if f not in excluded]


def _run_sql_for_iteration(
    sql_filename: str,
    substitution_vars: dict[str, Any],
    bq_client: bigquery.Client,
    project_region: str,
) -> tuple[pd.DataFrame, float]:
    """Replace query variables and execute one query using the BQ client for a single (project, region) iteration.

    Returns (df, elapsed_seconds). The elapsed time is wall-clock for the BQ
    query + result download, surfaced to the caller for per-SQL progress logging.
    """
    raw_sql = _load_resource_text(sql_filename)
    compiled_sql = substitute(raw_sql, substitution_vars)
    logger.debug(f"Running {sql_filename} for {project_region}")
    start = time.monotonic()
    df = bq_client.query(compiled_sql).to_dataframe()
    elapsed = time.monotonic() - start
    df["source"] = f"{project_region}_{sql_filename}"
    return df, elapsed


def _run_iteration(
    project_id: str,
    region: str,
    sql_files: list[str],
    profiling_window_days: int,
    max_parallel_sqls: int,
    accumulators: dict[str, list[pd.DataFrame]],
    accumulator_lock: threading.Lock,
    bigquery_client_factory: Callable[[str, str], bigquery.Client],
) -> None:
    project_region = f"{project_id}.region-{region}"
    bq_client = bigquery_client_factory(project_id, region)
    substitution_vars: dict[str, Any] = {
        "project_region": project_region,
        "profiling_window_in_days": profiling_window_days,
    }

    iter_start = time.monotonic()
    iter_rows = 0
    logger.info(f"[{project_region}] starting {len(sql_files)} SQLs (max_parallel={max_parallel_sqls})")

    with ThreadPoolExecutor(max_workers=max_parallel_sqls) as executor:
        future_to_file = {
            executor.submit(
                _run_sql_for_iteration, sql_filename, substitution_vars, bq_client, project_region
            ): sql_filename
            for sql_filename in sql_files
        }
        for future in as_completed(future_to_file):
            sql_filename = future_to_file[future]
            analysis_type = SQL_FILE_TO_ANALYSIS_TYPE[sql_filename]
            try:
                df, elapsed = future.result()
            except Exception as exc:
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
    columns = {}
    for f in fields:
        name = f["name"]
        dtype = _BQ_TYPE_TO_PANDAS.get(f["type"].lower(), "object")
        columns[name] = pd.Series(dtype=dtype)
    if columns:
        columns["source"] = pd.Series(dtype="object")
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


def execute(
    credential_manager: CredentialManager,
    bigquery_client_factory: Callable[[str, str], bigquery.Client],
    db_path: str,
) -> None:
    bq_settings = credential_manager.get_credentials("bigquery")

    pairs: list[dict[str, str]] = bq_settings["pairs"]
    profiler_cfg: dict[str, Any] = bq_settings.get("profiler", {})
    profiling_window_days: int = int(profiler_cfg.get("profiling_window_days", 180))
    max_parallel_sqls: int = int(profiler_cfg.get("max_parallel_sqls", 8))

    wall_clock_start = time.monotonic()
    try:
        sql_files = _select_sql_files(profiler_cfg)
        if not sql_files:
            raise RuntimeError("All SQL files excluded by config; nothing to extract.")

        analysis_types = json.loads(_load_resource_text("analysis_types.json"))

        accumulators: dict[str, list[pd.DataFrame]] = {at: [] for at in set(SQL_FILE_TO_ANALYSIS_TYPE.values())}
        accumulator_lock = threading.Lock()

        pair_statuses: list[dict[str, str]] = []
        for pair in pairs:
            project_id, region = pair["project"], pair["region"]
            logger.info(f"Extracting from project={project_id} region={region}")
            try:
                _run_iteration(
                    project_id=project_id,
                    region=region,
                    sql_files=sql_files,
                    profiling_window_days=profiling_window_days,
                    max_parallel_sqls=max_parallel_sqls,
                    accumulators=accumulators,
                    accumulator_lock=accumulator_lock,
                    bigquery_client_factory=bigquery_client_factory,
                )
            except Exception as exc:
                # Soft-fail a single (project, region): one missing IAM grant or unreadable
                # region must not abort sizing for every other project — bulk multi-project
                # profiling is the headline use case. The failure is surfaced per-pair below;
                # results from the pairs that did succeed are still written to DuckDB.
                logger.warning(f"Extraction failed for project={project_id} region={region}: {exc}")
                pair_statuses.append({"project": project_id, "region": region, "status": "error", "message": str(exc)})
                continue
            pair_statuses.append({"project": project_id, "region": region, "status": "success"})

        # Nothing salvageable if every pair failed — raise so the top-level handler reports a
        # structured error instead of "success" over an empty / stub-only DuckDB.
        if pairs and all(status["status"] == "error" for status in pair_statuses):
            raise RuntimeError("Extraction failed for all (project, region) pairs; see per-pair errors above.")

        row_counts = _write_accumulators(accumulators, db_path, analysis_types)

        wall_clock_seconds = round(time.monotonic() - wall_clock_start, 2)
        logger.info(f"Total wall-clock: {wall_clock_seconds}s")
        # Final stdout line is the structured payload that pipeline._run_python_script parses
        # to decide success/error. Keep the `print` (matching Synapse's workspace_extract.py
        # convention) — don't replace with logger.info because pipeline reads the last raw line.
        print(
            json.dumps(
                {
                    "status": "success",
                    "message": "BigQuery metadata extract complete",
                    "tables": sorted(row_counts.keys()),
                    "rows": row_counts,
                    "pairs": pair_statuses,
                    "wall_clock_seconds": wall_clock_seconds,
                }
            )
        )
    # Synapse pattern: catch-all at top level to produce a structured error payload that
    # pipeline.py can parse. Internal failures already propagate with specific types.
    except Exception as exc:
        logger.error(f"BigQuery metadata extract failed: {exc}")
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        sys.exit(1)


def create_bigquery_client(project_id: str, region: str) -> bigquery.Client:
    """Create a google-cloud-bigquery Client routed at the given project + region.

    Authentication uses the standard ADC chain (GOOGLE_APPLICATION_CREDENTIALS, gcloud
    application-default login, or the metadata server). Service-account impersonation is
    supported via ADC; see
    https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment#sa-impersonation.
    """
    return bigquery.Client(project=project_id, location=region)


if __name__ == "__main__":
    initialize_logging()
    _db_path, _creds_file = arguments_loader(desc="BigQuery Metadata Extract Script")
    execute(
        credential_manager=create_credential_manager(PRODUCT_NAME, EnvGetter()),
        bigquery_client_factory=create_bigquery_client,
        db_path=_db_path,
    )
