-- This procedure runs through timelined historical workloads on BQ
-- and generates an aggregated view of slot utilization
-- split between ETL and BI
WITH SLOT_USAGE_PER_SEC AS
(
  SELECT
  period_start,
  period_slot_ms,
  job_type,
  statement_type
FROM
  `{{project_region}}`.INFORMATION_SCHEMA.JOBS_TIMELINE
WHERE
  period_start BETWEEN TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {{profiling_window_in_days}} DAY) AND CURRENT_TIMESTAMP()
  AND (statement_type != 'SCRIPT' OR statement_type IS NULL)
),
STATEMENT_CLASSIFICATION AS
(
SELECT * FROM UNNEST([STRUCT<statement_type string, workload_category STRING>
  ("SELECT", "BI"),
  ("ASSERT", "ETL"),
  ("INSERT", "ETL"),
  ("UPDATE", "ETL"),
  ("DELETE", "ETL"),
  ("MERGE", "ETL"),
  ("CREATE_TABLE", "ETL"),
  ("CREATE_TABLE_AS_SELECT", "ETL"),
  ("CREATE_VIEW","ETL"),
  ("CREATE_MODEL", "ML"),
  ("CREATE_MATERIALIZED_VIEW", "ETL"),
  ("CREATE_FUNCTION", "ETL"),
  ("CREATE_PROCEDURE", "ETL"),
  ("CREATE_SCHEMA", "ETL"),
  ("DROP_TABLE", "ETL"),
  ("DROP_EXTERNAL_TABLE", "ETL"),
  ("DROP_VIEW", "ETL"),
  ("DROP_MATERIALIZED_VIEW", "ETL"),
  ("DROP_FUNCTION", "ETL"),
  ("DROP_PROCEDURE", "ETL"),
  ("DROP_SCHEMA", "ETL"),
  ("ALTER_TABLE", "ETL"),
  ("ALTER_VIEW", "ETL"),
  ("ALTER_MATERIALIZED_VIEW", "ETL"),
  ("SCRIPT", "ETL"),
  ("TRUNCATE_TABLE", "ETL"),
  ("CREATE_EXTERNAL_TABLE", "ETL"),
  ("EXPORT_DATA", "ETL"),
  ("CALL", "ETL"),
  ("QUERY_STATEMENT_TYPE_UNSPECIFIED", "Other"),
(null, "BI")
  ])
),
-- each second may be repeated per job
-- hence, aggregation by period_start is important
SLOT_USAGE_PER_SEC_BY_WORKLOAD
AS
(
  SELECT s.period_start,
          SUM(s.period_slot_ms) as period_slot_ms,
          CASE
          WHEN stmt.workload_category is NULL AND s.job_type = 'QUERY' THEN 'BI'
          WHEN stmt.workload_category is NULL AND s.job_type = 'COPY' THEN 'ETL'
          WHEN stmt.workload_category is NULL AND s.job_type = 'LOAD' THEN 'ETL'
          ELSE stmt.workload_category
          END as workload_type
          FROM SLOT_USAGE_PER_SEC s
          LEFT JOIN STATEMENT_CLASSIFICATION stmt
          ON s.statement_type = stmt.statement_type
          GROUP BY s.period_start, workload_type
),
-- num_slots_in_period = total_slot_secs in the period / secs in the period
SLOT_USAGE_AGGREGATED_BY_WORKLOAD
AS
(
  SELECT FORMAT_TIMESTAMP('%Y-%m-%dT%H', period_start) as time_window,
          SUM(s.period_slot_ms) / 1000.0 as slot_secs,
          CAST(COUNT(1) as FLOAT64) as cumulative_secs_spent_in_exec,
          SUM(s.period_slot_ms) / (1000.0 * CAST(COUNT(1) as FLOAT64)) AS slots_avg,
          APPROX_QUANTILES(s.period_slot_ms / 1000.0, 100) [OFFSET(50)] AS slots_perc_50th,
          APPROX_QUANTILES(s.period_slot_ms / 1000.0, 100) [OFFSET(90)] AS slots_perc_90th,
          APPROX_QUANTILES(s.period_slot_ms / 1000.0, 100) [OFFSET(99)] AS slots_perc_99th,
          MAX(s.period_slot_ms / 1000.0) AS slots_max,
          workload_type
          FROM SLOT_USAGE_PER_SEC_BY_WORKLOAD s
          GROUP BY FORMAT_TIMESTAMP('%Y-%m-%dT%H', period_start),
          workload_type
)
SELECT time_window,
'{{project_region}}' AS metadata_level,
workload_type,
slot_secs,
slots_avg,
slots_perc_50th,
slots_perc_90th,
slots_perc_99th,
slots_max,
cumulative_secs_spent_in_exec
FROM SLOT_USAGE_AGGREGATED_BY_WORKLOAD
ORDER BY time_window, workload_type;
