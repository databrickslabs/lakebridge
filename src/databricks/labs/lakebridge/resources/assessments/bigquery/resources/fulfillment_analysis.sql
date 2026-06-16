-- This procedure runs through the historical job runs on BQ
-- and generates a time-series view of slot fulfillment
-- split between ETL and BI

WITH STATEMENT_CLASSIFICATION AS
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
jobs
as
(
  select project_id,
  creation_time as job_creation_time,
  job_type,
  job.statement_type,
  workload_category,
  start_time as job_start_time,
  end_time as job_end_time,
  TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS job_duration_ms,
  reservation_id,
  total_slot_ms AS job_total_slot_ms,
  job_id,
  timeline
  from `{{project_region}}`.INFORMATION_SCHEMA.JOBS AS job
  LEFT JOIN STATEMENT_CLASSIFICATION stmt
  ON job.statement_type = stmt.statement_type
  WHERE DATE(creation_time) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL {{profiling_window_in_days}} DAY) AND CURRENT_DATE()
  AND (job.statement_type != 'SCRIPT' OR job.statement_type IS NULL)
),
jobs_timeline_flattened as
(
  select *,
  -- Average slot utilization per job is calculated by dividing total_slot_ms by the millisecond duration of the job
  -- The average slots might be 55. But a single stage might spike to 2000 slots.
  -- This is important to know when estimating number of slots to purchase.
  SAFE_DIVIDE(job_total_slot_ms, job_duration_ms) as job_slots
   from jobs
  CROSS JOIN UNNEST(timeline) AS unnest_timeline WITH OFFSET AS timeline_order
),
-- analyzing each phase in a job's timeline
jobs_timeline_phased
as
(
  select project_id,
  job_creation_time,
  job_type,
  statement_type,
  workload_category,
  job_start_time,
  job_end_time,
  job_duration_ms,
  reservation_id,
  job_total_slot_ms,
  job_id,
  timeline_order,
  elapsed_ms as time_elapsed_since_query_start,
  -- time duration of a phase = gap between time elapsed since the start of query execution measured during the current phase vs the prior phase
  elapsed_ms - coalesce(lag(elapsed_ms) over (partition by job_id order by timeline_order), 0.0) as phase_duration_ms,
  total_slot_ms as cumu_slot_ms_since_query_start,
  -- slots used in a phase = slot-time for a phase / time duration of a phase
  total_slot_ms - coalesce(lag(total_slot_ms) over (partition by job_id order by timeline_order), 0.0) as phase_slot_ms,
  job_slots,
  coalesce(estimated_runnable_units, 0) as slots_requested_but_not_received
  from jobs_timeline_flattened
),
slot_fulfillment_analysis
AS
(
  SELECT *,
  TIMESTAMP_ADD(job_creation_time, INTERVAL time_elapsed_since_query_start MILLISECOND) AS phase_timestamp,
  SAFE_DIVIDE(phase_slot_ms, phase_duration_ms) as phase_slots_fulfilled,
  slots_requested_but_not_received + coalesce(SAFE_DIVIDE(phase_slot_ms, phase_duration_ms), 0.0) AS phase_slots_requested
  FROM jobs_timeline_phased
)

SELECT '{{project_region}}' AS metadata_level,
FORMAT_TIMESTAMP('%Y-%m-%dT%H', phase_timestamp) as time_window,
workload_category,
SUM(phase_slots_requested) as slots_requested,
SUM(phase_slots_fulfilled) as slots_fulfilled,
 FROM slot_fulfillment_analysis
 GROUP BY project_id, FORMAT_TIMESTAMP('%Y-%m-%dT%H', phase_timestamp), workload_category
 order by time_window
;
