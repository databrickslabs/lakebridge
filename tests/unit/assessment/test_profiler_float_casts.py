"""Assert profiler SQL retains float casts that prevent DuckDB narrow-DECIMAL overflow (PR #2578).

Snippets are matched after whitespace normalization; case is preserved.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ASSESSMENTS = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments"

REQUIRED_CASTS: dict[str, tuple[str, ...]] = {
    "bigquery/resources/workload_types.sql": (
        "CAST(sum(slot_ms) AS FLOAT64)",
        "CAST(sum(bytes_processed) AS FLOAT64)",
    ),
    "legacy_synapse/storage_info.sql": (
        "(SUM(reserved_page_count) * 8.0) / 1024.0 AS ReservedSpaceMB",
        "(SUM(used_page_count) * 8.0) / 1024.0 AS UsedSpaceMB",
    ),
    "redshift/sql/1_rs_spectrum_tb_month.sql": (
        "round(sum(returned_bytes)/(1024.0*1024*1024*1024),4)::double precision s3_scanned_tb_month",
        "round(avg(s3_scanned_tb_month) over (), 4 )::double precision avg_daily_scanned_tb",
    ),
    "redshift/sql/2_rs_managed_storage_gb_stv.sql": (
        "round((sum(used) / 1024), 2)::double precision as rs_managed_storage_gb",
    ),
    "redshift/sql/2_rs_managed_storage_gb_serverless.sql": (
        "round(avg(data_storage) / 1024.0, 2)::double precision as rs_managed_storage_gb",
    ),
    "redshift/sql/3_rs_nodes_serverless.sql": ("sum(compute_seconds)::double precision as compute_seconds",),
    "redshift/sql/4_rs_avg_concurrent_users.sql": (
        "round(avg(distinct_users),0)::double precision avg_concurrent_users",
    ),
    "redshift/sql/5_rs_avg_queries_minute.sql": ("avg(query_cnt)::double precision avg_queries_minute",),
    "redshift/sql/7_chart_cpu_consumption_by_query_type.sql": (
        "duration/1000.0 as run_time_ms",
        "sum(run_time_ms)::double precision as sum_cpu_time",
    ),
    "redshift/sql/9_chart_cpu_consumption_by_hour_and_query_type.sql": (
        "duration/1000.0 as run_time_ms",
        "sum(run_time_ms)::double precision as sum_cpu_time",
    ),
    "snowflake/automatic_clustering.sql": (
        "CREDITS_USED::DOUBLE AS CREDITS_USED",
        "NUM_BYTES_RECLUSTERED::DOUBLE AS NUM_BYTES_RECLUSTERED",
        "NUM_ROWS_RECLUSTERED::DOUBLE AS NUM_ROWS_RECLUSTERED",
    ),
    "snowflake/materialized_view_refresh.sql": ("CREDITS_USED::DOUBLE AS CREDITS_USED",),
    "snowflake/pipe_usage.sql": (
        "CREDITS_USED::DOUBLE AS CREDITS_USED",
        "BYTES_INSERTED::DOUBLE AS BYTES_INSERTED",
    ),
    "snowflake/query_history.sql": (
        "TOTAL_ELAPSED_TIME::DOUBLE AS TOTAL_ELAPSED_TIME",
        "EXECUTION_TIME::DOUBLE AS EXECUTION_TIME",
        "COMPILATION_TIME::DOUBLE AS COMPILATION_TIME",
        "QUEUED_PROVISIONING_TIME::DOUBLE AS QUEUED_PROVISIONING_TIME",
        "QUEUED_REPAIR_TIME::DOUBLE AS QUEUED_REPAIR_TIME",
        "QUEUED_OVERLOAD_TIME::DOUBLE AS QUEUED_OVERLOAD_TIME",
        "TRANSACTION_BLOCKED_TIME::DOUBLE AS TRANSACTION_BLOCKED_TIME",
        "BYTES_SCANNED::DOUBLE AS BYTES_SCANNED",
        "BYTES_WRITTEN::DOUBLE AS BYTES_WRITTEN",
        "BYTES_SPILLED_TO_LOCAL_STORAGE::DOUBLE AS BYTES_SPILLED_TO_LOCAL_STORAGE",
        "BYTES_SPILLED_TO_REMOTE_STORAGE::DOUBLE AS BYTES_SPILLED_TO_REMOTE_STORAGE",
        "ROWS_PRODUCED::DOUBLE AS ROWS_PRODUCED",
        "CREDITS_USED_CLOUD_SERVICES::DOUBLE AS CREDITS_USED_CLOUD_SERVICES",
    ),
    "snowflake/query_samples.sql": ("TOTAL_ELAPSED_TIME::DOUBLE AS TOTAL_ELAPSED_TIME",),
    "snowflake/rate_sheet.sql": ("AVG(EFFECTIVE_RATE)::DOUBLE AS AVG_EFFECTIVE_RATE",),
    "snowflake/storage_usage.sql": (
        "AVERAGE_DATABASE_BYTES::DOUBLE AS AVERAGE_DATABASE_BYTES",
        "AVERAGE_FAILSAFE_BYTES::DOUBLE AS AVERAGE_FAILSAFE_BYTES",
        "(AVERAGE_DATABASE_BYTES / (1024*1024*1024))::DOUBLE as STORAGE_GB",
        "(AVERAGE_FAILSAFE_BYTES / (1024*1024*1024))::DOUBLE as FAILSAFE_GB",
    ),
    "snowflake/warehouse_usage.sql": (
        "CREDITS_USED::DOUBLE AS CREDITS_USED",
        "CREDITS_USED_COMPUTE::DOUBLE AS CREDITS_USED_COMPUTE",
        "CREDITS_USED_CLOUD_SERVICES::DOUBLE AS CREDITS_USED_CLOUD_SERVICES",
    ),
}


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql)


def _cases() -> list[tuple[str, str]]:
    return [(rel_path, snippet) for rel_path, snippets in REQUIRED_CASTS.items() for snippet in snippets]


@pytest.mark.parametrize(("rel_path", "snippet"), _cases(), ids=lambda v: v if "/" in v or "::" in v else None)
def test_profiler_sql_retains_float_cast(rel_path: str, snippet: str) -> None:
    sql_file = _ASSESSMENTS / rel_path
    assert sql_file.exists(), f"expected profiler SQL file is missing: {sql_file}"

    haystack = _normalize(sql_file.read_text(encoding="utf-8"))
    needle = _normalize(snippet)
    assert needle in haystack, f"{rel_path} is missing required float cast: {snippet}"
