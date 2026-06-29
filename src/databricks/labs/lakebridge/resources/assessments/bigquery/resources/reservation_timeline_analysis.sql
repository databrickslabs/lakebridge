-- This SQL script performs an analysis on the reservation timeline in BigQuery.
--
-- Parameters:
--   metadata_level: STRING - The metadata level, set to '{{project_region}}'.
--   profiling_window_in_days: INT64 - The number of days for the profiling window, default is 180 days.
--
-- The script selects various fields from the INFORMATION_SCHEMA.RESERVATIONS_TIMELINE table within the specified profiling window.
--
-- Selected Fields:
--   metadata_level: The metadata level for the reservation.
--   period_start: The start time of the reservation period.
--   reservation_id: The ID of the reservation.
--   slots_assigned: The number of slots assigned to the reservation.
--   slots_max_assigned: The maximum number of slots assigned to the reservation.
--   autoscale_current_slots: The current number of slots in autoscale.
--   autoscale_max_slots: The maximum number of slots in autoscale.
--   ignore_idle_slots: Indicates whether idle slots are ignored.
--   edition: The edition of the reservation.
--
-- The WHERE clause filters the results to include only those records where the period_start is within the profiling window.
select
  '{{project_region}}' as metadata_level,
  period_start,
  TO_HEX(MD5(reservation_id)) as reservation_id_hash,
  slots_assigned,
  slots_max_assigned,
  autoscale.current_slots as autoscale_current_slots,
  autoscale.max_slots as autoscale_max_slots,
  ignore_idle_slots,
  edition
  from `{{project_region}}.INFORMATION_SCHEMA.RESERVATIONS_TIMELINE`
  WHERE
period_start BETWEEN TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {{profiling_window_in_days}} DAY) AND CURRENT_TIMESTAMP()
