select TIMESTAMP_TRUNC(period_start, MINUTE) as time_window_min,
SUM(period_slot_ms) AS period_slot_ms,
SUM(period_slot_ms) / (60.0 * 1000.0) as slots,
'{{project_region}}' as metadata_level,
TO_HEX(MD5(reservation_id)) as reservation_id_hash
from `{{project_region}}.INFORMATION_SCHEMA.JOBS_TIMELINE`
WHERE
period_start BETWEEN TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {{profiling_window_in_days}} DAY) AND CURRENT_TIMESTAMP()
AND (statement_type != "SCRIPT" OR statement_type IS NULL)
GROUP BY
  TIMESTAMP_TRUNC(period_start, MINUTE),
  metadata_level,
  reservation_id
