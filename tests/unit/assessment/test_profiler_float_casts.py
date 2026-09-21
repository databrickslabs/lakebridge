"""Assert profiler SQL retains float casts that prevent DuckDB narrow-DECIMAL overflow (PR #2578).

Every float/double cast in each referenced query is enumerated below, so removing any single
cast fails CI. Snippets are matched after whitespace normalization; case is preserved.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ASSESSMENTS = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments"

REQUIRED_CASTS: dict[str, tuple[str, ...]] = {
    "synapse/common/queries.py": (
        "CAST(RESOURCE_ALLOCATION_PERCENTAGE AS FLOAT) AS RESOURCE_ALLOCATION_PERCENTAGE",
        "CAST(AU.TOTAL_PAGES * 8.0 / 1024 AS FLOAT) AS TOTAL_SIZE_MB",
        "CAST(AU.USED_PAGES * 8.0 / 1024 AS FLOAT) AS USED_SIZE_MB",
        "CAST(QS.TOTAL_ELAPSED_TIME / 1000000.0 AS FLOAT) AS TOTAL_ELAPSED_TIME_SEC",
        "CAST(QS.TOTAL_WORKER_TIME / 1000000.0 AS FLOAT) AS TOTAL_WORKER_TIME_SEC",
        "CAST(QS.LAST_ELAPSED_TIME / 1000000.0 AS FLOAT) AS LAST_ELAPSED_TIME_SEC",
        "CAST(QS.LAST_WORKER_TIME / 1000000.0 AS FLOAT) AS LAST_WORKER_TIME_SEC",
    ),
    "oracle/config_storage.sql": (
        "CAST(SUM(gb) AS BINARY_DOUBLE) AS gb",
        "CAST(SUM(freegb) AS BINARY_DOUBLE) AS freegb",
        "CAST(SUM(maxgb) AS BINARY_DOUBLE) AS maxgb",
    ),
    "oracle/perf_heatmap.sql": ("CAST(LOAD AS BINARY_DOUBLE) AS value",),
    "oracle/perf_sqltext.sql": ("CAST(sum(elapsed_time)/1000000 AS BINARY_DOUBLE) as total_run_time_secs",),
    "teradata/td_sys_usage_agg.sql": (
        "CAST(round(avg(totNCPUs), 0) AS FLOAT) as totNCPUs",
        "CAST(round(avg(totVproc1), 0) AS FLOAT) as totVproc1",
        "CAST(round(avg(totCPUUExec), 0) AS FLOAT) as totCPUUExec",
        "CAST(round(avg(totCPUUServ), 0) AS FLOAT) as totCPUUServ",
        "CAST(round(avg(totCPUIoWait), 0) AS FLOAT) as totCPUIoWait",
        "CAST(round(avg(totMemSizeMB), 0) AS FLOAT) as totMemSizeMB",
        "CAST(round(avg(totCPUIdle), 0) AS FLOAT) as totCPUIdle",
        "CAST(round(avg(totMemFreeMB), 0) AS FLOAT) as totMemFreeMB",
    ),
    "teradata/td_sys_disk_utilization.sql": (
        "CAST(SUM((MAXPERM) /(1024 * 1024)) AS FLOAT) MAX_PERM_MB",
        "CAST(SUM((CURRENTPERM) /(1024 * 1024)) AS FLOAT) CURRENT_PERM_MB",
        "CAST(SUM((MAXSPOOL) /(1024 * 1024)) AS FLOAT) MAX_SPOOL_MB",
        "CAST(SUM((CURRENTSPOOL) /(1024 * 1024)) AS FLOAT) CURRENT_SPOOL_MB",
    ),
    "teradata/core/td_dbql_core_info_extract.sql": (
        "CAST((LogTbl.AMPCPUTime + LogTbl.ParserCPUTime + LogTbl.DisCPUTime) AS FLOAT) as TotalCPUTime",
    ),
    "teradata/pdcr/td_pdcr_info_agg_extract.sql": (
        "CAST(SUM(AMPCPUTime) AS FLOAT) AS SumCPU",
        "CAST(AVG(AMPCPUTime) AS FLOAT) AS AvgCPU",
        "CAST(MAX(AMPCPUTime) AS FLOAT) AS MaxCPU",
        "CAST(SUM(TotalIOCount) AS FLOAT) AS SumIO",
        "CAST(AVG(TotalIOCount) AS FLOAT) AS AvgIO",
        "CAST(MAX(TotalIOCount) AS FLOAT) AS MaxIO",
        "CAST(MAX(DelayTime) AS FLOAT) AS MaxTDWMDelayTime",
        "CAST(SUM(DelayTime) AS FLOAT) AS SumTDWMDelayTime",
        "CAST(AVG(ResponseSecs) AS FLOAT) AS AvgRespSecs",
        "CAST(MAX(ResponseSecs) AS FLOAT) AS MaxRespSecs",
    ),
    "teradata/pdcr/td_pdcr_sp_exe_info_agg_extract.sql": (
        "CAST(avg(AMPCPUTime) AS FLOAT) avgAMPCPUTime",
        "CAST(avg(ExecutionSecs) AS FLOAT) avgExecutionSecs",
        "CAST(avg(NumStatements) AS FLOAT) NumStatements",
    ),
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
