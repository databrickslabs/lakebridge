"""
Step 2 of the Lakebridge BigQuery profiler pipeline.

Runs after `bq_metadata_extract.py` against the same DuckDB file. Builds the
cost-projection layer used by the Lakeview dashboard:

    input_params                — one-row table of tuning constants for the chosen
                                  target Databricks cloud (aws / azure / gcp).
    bq_slots_pricing_analysis   — per-(metadata_level, time_window) cost projection
                                  with all percentile families (avg / 50th / 90th /
                                  99th / max / perf_based).
    monthly_weighted_pricing    — bq_slots_pricing_analysis rolled up monthly per
                                  price_percentile via UNION ALL.
    consumption_by_commitment   — VIEW unioning consumption_beyond_commitments and
                                  consumption_through_commitments with a
                                  commitment_used boolean.

No BigQuery client is needed at this step — everything runs in-process via DuckDB
on tables step 1 already loaded.
"""

import importlib.resources as pkg_resources
import json
import logging
import sys
import time
from typing import Any

import duckdb
import pandas as pd

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.resources.assessments.bigquery.common.duckdb_helpers import insert_df_to_duckdb
from databricks.labs.lakebridge.resources.assessments.bigquery.common.functions import arguments_loader
from databricks.labs.lakebridge.resources.assessments.bigquery.common.tuning_params import TUNING_INPUT_PARAMS


# Use the canonical dotted name so INFO logs surface under `python -m` invocation —
# same fix applied to bq_metadata_extract.py.
logger = logging.getLogger("databricks.labs.lakebridge.resources.assessments.bigquery.bq_pricing_analysis")


_BIGQUERY_RESOURCES = "databricks.labs.lakebridge.resources.assessments.bigquery"

# Ordered: must run bq_slots_pricing_analysis first since monthly_weighted_pricing reads it.
_SQL_FILES_IN_ORDER: tuple[str, ...] = (
    "bq_slots_pricing_analysis.sql",
    "monthly_weighted_pricing.sql",
    "consumption_by_commitment.sql",
)


def _load_resource_text(filename: str) -> str:
    return pkg_resources.files(f"{_BIGQUERY_RESOURCES}.sql").joinpath(filename).read_text(encoding="utf-8")


def _build_input_params_row(target_cloud: str, params: dict[str, Any]) -> pd.DataFrame:
    """Materialize the input_params table — one row per profiling run."""
    return pd.DataFrame([{**params, "target_cloud": target_cloud}])


def _count_rows(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    """COUNT(*) helper that satisfies mypy — `fetchone()` returns `tuple | None`,
    but on `SELECT count(*) FROM ...` against a real connection it's never None."""
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def execute() -> None:
    db_path, _creds_file = arguments_loader(desc="BigQuery Pricing Analysis Script")

    cred_manager = create_credential_manager(PRODUCT_NAME, EnvGetter())
    bq_settings = cred_manager.get_credentials("bigquery")
    profiler_cfg = bq_settings.get("profiler", {})

    # `exclude_pricing_analysis` lets customers opt out of step 2 (e.g. when they just
    # want raw extracts and will run pricing logic themselves). Mirrors how Synapse's
    # extract scripts return early on their respective exclusion flags.
    if profiler_cfg.get("exclude_pricing_analysis"):
        msg = "exclude_pricing_analysis is set; skipping step 2."
        logger.info(msg)
        print(json.dumps({"status": "skipped", "message": msg}))
        return

    target_cloud = str(bq_settings.get("target_cloud", "gcp")).lower()

    if target_cloud not in TUNING_INPUT_PARAMS:
        msg = f"Unknown target_cloud '{target_cloud}'; expected one of {sorted(TUNING_INPUT_PARAMS)}."
        logger.error(msg)
        print(json.dumps({"status": "error", "message": msg}), file=sys.stderr)
        sys.exit(1)

    params = TUNING_INPUT_PARAMS[target_cloud]

    wall_clock_start = time.monotonic()
    try:
        # 1. Insert the input_params table so the dashboard can read tuning knobs back out.
        input_params_df = _build_input_params_row(target_cloud, dict(params))
        insert_df_to_duckdb(input_params_df, db_path, "input_params")

        # 2. Run the derived-table SQL files. {var} placeholders are substituted from `params`
        # plus `target_cloud` (used in the join predicates of bq_slots_pricing_analysis.sql).
        # consumption_by_commitment.sql now always runs — step 1 writes empty stub tables for
        # excluded analysis_types so the UNION ALL view builds cleanly even for Pay-as-you-go
        # customers with no reservation data.
        substitutions = {**params, "target_cloud": target_cloud}
        with duckdb.connect(db_path) as conn:
            for sql_filename in _SQL_FILES_IN_ORDER:
                raw = _load_resource_text(sql_filename)
                compiled = raw.format(**substitutions)
                step_start = time.monotonic()
                conn.execute(compiled)
                elapsed = time.monotonic() - step_start
                logger.info(f"executed {sql_filename} in {elapsed:.1f}s")

        # 3. Row counts for the success payload — view counts are included for visibility.
        produced_tables = [
            "input_params",
            "bq_slots_pricing_analysis",
            "monthly_weighted_pricing",
            "consumption_by_commitment",
        ]
        with duckdb.connect(db_path, read_only=True) as conn:
            row_counts: dict[str, int] = {t: _count_rows(conn, t) for t in produced_tables}

        wall_clock_seconds = round(time.monotonic() - wall_clock_start, 2)
        logger.info(f"Total wall-clock: {wall_clock_seconds}s")
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
    # Synapse / step 1 pattern: catch-all at top level produces a structured error payload
    # that pipeline.py can parse. Internal failures (DuckDB errors, KeyError on tuning params)
    # already propagate with specific types; this is a safety net.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(f"BigQuery pricing analysis failed: {exc}")
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    initialize_logging()
    execute()
