-- This SQL script performs an analysis on the reservation timeline in BigQuery.
-- 
-- Parameters:
--   metadatalevel: STRING - The metadata level, set to 'my-gcp-project.region-us'.
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
DECLARE metadatalevel STRING;
DECLARE profiling_window_in_days INT64 DEFAULT 180;

SET metadatalevel = 'my-gcp-project.region-us';
SET profiling_window_in_days = 180;

select 
  metadatalevel as metadata_level,
  period_start,
  TO_HEX(MD5(reservation_id)) as reservation_id_hash,
  slots_assigned,
  slots_max_assigned,
  autoscale.current_slots as autoscale_current_slots,
  autoscale.max_slots as autoscale_max_slots,
  ignore_idle_slots,
  edition
  from `my-gcp-project.region-us.INFORMATION_SCHEMA.RESERVATIONS_TIMELINE`
  WHERE 
period_start BETWEEN TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL profiling_window_in_days DAY) AND CURRENT_TIMESTAMP()