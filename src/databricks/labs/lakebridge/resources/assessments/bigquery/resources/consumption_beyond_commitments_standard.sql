-- This script shows billed slot seconds NOT covered by a commitment_plan.
-- It needs to run in the same project the reservations were created in. 
-- Editions options are STANDARD, ENTERPRISE, and ENTERPRISE PLUS.

/*
This script has several parts:
1. Calculate the baseline and scaled slots for reservations
2. Calculate the committed slots
3. Join the two results above to calculate the baseline not covered by committed
   slots
4. Aggregate the number
*/

-- variables
DECLARE start_time, end_time TIMESTAMP;
DECLARE metadatalevel STRING;

DECLARE
  edition_to_check STRING;

-- This script declares a variable `profiling_window_in_days` with a default value of 180.
-- The variable is of type INT64 and is used to define the profiling window in days.
DECLARE profiling_window_in_days INT64 DEFAULT 180; 

/* Google uses Pacific Time to calculate the billing period for all customers,
regardless of their time zone. Use the following format if you want to match the
billing report. Change the start_time and end_time values to match the desired
window. */

/* The following three variables (start_time, end_time, and edition_to_check)
are the only variables that you need to set in the script.

During daylight savings time, the start_time and end_time variables should
follow this format: 2024-02-20 00:00:00-08. */

SET profiling_window_in_days = {{profiling_window_in_days}};
SET start_time = current_timestamp() - INTERVAL profiling_window_in_days DAY;
SET end_time = current_timestamp();
SET edition_to_check = 'STANDARD';
SET metadatalevel = '{{project_region}}';

/*
The following function returns the slot seconds for the time window between
two capacity changes. For example, if there are 100 slots between (2023-06-01
10:00:00, 2023-06-01 11:00:00), then during that window the total slot seconds
will be 100 * 3600.

This script calculates a specific window (based on the variables defined above),
which is why the following script includes script_start_timestamp_unix_millis
and script_end_timestamp_unix_millis. */

CREATE TEMP FUNCTION GetSlotSecondsBetweenChanges(
  slots FLOAT64,
  range_begin_timestamp_unix_millis FLOAT64,
  range_end_timestamp_unix_millis FLOAT64,
  script_start_timestamp_unix_millis FLOAT64,
  script_end_timestamp_unix_millis FLOAT64)
RETURNS INT64
LANGUAGE js
AS r"""
    if (script_end_timestamp_unix_millis < range_begin_timestamp_unix_millis || script_start_timestamp_unix_millis > range_end_timestamp_unix_millis) {
      return 0;
    }
    var begin = Math.max(script_start_timestamp_unix_millis, range_begin_timestamp_unix_millis)
    var end = Math.min(script_end_timestamp_unix_millis, range_end_timestamp_unix_millis)
    return slots * Math.ceil((end - begin) / 1000.0)
""";
/*
Sample RESERVATION_CHANGES data (unrelated columns ignored):
+---------------------+------------------+--------+---------------+---------------+
|  change_timestamp   | reservation_name | action | slot_capacity | current_slots |
+---------------------+------------------+--------+---------------+---------------+
| 2023-07-27 22:24:15 | res1             | CREATE |           300 |             0 |
| 2023-07-27 22:25:21 | res1             | UPDATE |           300 |           180 |
| 2023-07-27 22:39:14 | res1             | UPDATE |           300 |           100 |
| 2023-07-27 22:40:20 | res2             | CREATE |           300 |             0 |
| 2023-07-27 22:54:18 | res2             | UPDATE |           300 |           120 |
| 2023-07-27 22:55:23 | res1             | UPDATE |           300 |             0 |

Sample CAPACITY_COMMITMENT_CHANGES data (unrelated columns ignored):
+---------------------+------------------------+-----------------+--------+------------+--------+
|  change_timestamp   | capacity_commitment_id | commitment_plan | state  | slot_count | action |
+---------------------+------------------------+-----------------+--------+------------+--------+
| 2023-07-20 19:30:27 | 12954109101902401697   | ANNUAL          | ACTIVE |        100 | CREATE |
| 2023-07-27 22:29:21 | 11445583810276646822   | FLEX            | ACTIVE |        100 | CREATE |
| 2023-07-27 23:10:06 | 7341455530498381779    | MONTHLY         | ACTIVE |        100 | CREATE |
*/

WITH
  /*
  The scaled_slots & baseline change history:
  +---------------------+------------------+------------------------------+---------------------+
  |  change_timestamp   | reservation_name | autoscale_current_slot_delta | baseline_slot_delta |
  +---------------------+------------------+------------------------------+---------------------+
  | 2023-07-27 22:24:15 | res1             |                            0 |                 300 |
  | 2023-07-27 22:25:21 | res1             |                          180 |                   0 |
  | 2023-07-27 22:39:14 | res1             |                          -80 |                   0 |
  | 2023-07-27 22:40:20 | res2             |                            0 |                 300 |
  | 2023-07-27 22:54:18 | res2             |                          120 |                   0 |
  | 2023-07-27 22:55:23 | res1             |                         -100 |                   0 |
  */
  reservation_slot_data AS (
    SELECT
      change_timestamp,
      reservation_name,
      CASE action
        WHEN "CREATE" THEN autoscale.current_slots
        WHEN "UPDATE"
          THEN
            IFNULL(
              autoscale.current_slots - LAG(autoscale.current_slots)
                OVER (
                  PARTITION BY project_id, reservation_name
                  ORDER BY change_timestamp ASC, action ASC
                ),
              IFNULL(
                autoscale.current_slots,
                IFNULL(
                  -1 * LAG(autoscale.current_slots)
                    OVER (
                      PARTITION BY project_id, reservation_name
                      ORDER BY change_timestamp ASC, action ASC
                    ),
                  0)))
        WHEN "DELETE"
          THEN
            IF(
              LAG(action)
                OVER (
                  PARTITION BY project_id, reservation_name
                  ORDER BY change_timestamp ASC, action ASC
                )
                IN UNNEST(['CREATE', 'UPDATE']),
              -1 * autoscale.current_slots,
              0)
        END
        AS autoscale_current_slot_delta,
      CASE action
        WHEN "CREATE" THEN slot_capacity
        WHEN "UPDATE"
          THEN
            IFNULL(
              slot_capacity - LAG(slot_capacity)
                OVER (
                  PARTITION BY project_id, reservation_name
                  ORDER BY change_timestamp ASC, action ASC
                ),
              IFNULL(
                slot_capacity,
                IFNULL(
                  -1 * LAG(slot_capacity)
                    OVER (
                      PARTITION BY project_id, reservation_name
                      ORDER BY change_timestamp ASC, action ASC
                    ),
                  0)))
        WHEN "DELETE"
          THEN
            IF(
              LAG(action)
                OVER (
                  PARTITION BY project_id, reservation_name
                  ORDER BY change_timestamp ASC, action ASC
                )
                IN UNNEST(['CREATE', 'UPDATE']),
              -1 * slot_capacity,
              0)
        END
        AS baseline_slot_delta,
    FROM
      `{{project_region}}.INFORMATION_SCHEMA.RESERVATION_CHANGES`
    WHERE
      UPPER(edition) = edition_to_check
      AND change_timestamp <= end_time
  ),

  -- Convert the above to running total
  /*
  +---------------------+-------------------------+----------------+
  |  change_timestamp   | autoscale_current_slots | baseline_slots |
  +---------------------+-------------------------+----------------+
  | 2023-07-27 22:24:15 |                       0 |            300 |
  | 2023-07-27 22:25:21 |                     180 |            300 |
  | 2023-07-27 22:39:14 |                     100 |            300 |
  | 2023-07-27 22:40:20 |                     100 |            600 |
  | 2023-07-27 22:54:18 |                     220 |            600 |
  | 2023-07-27 22:55:23 |                     120 |            600 |
  */
  running_reservation_slot_data AS (
    SELECT
      change_timestamp,
      SUM(autoscale_current_slot_delta)
        OVER (ORDER BY change_timestamp RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        AS autoscale_current_slots,
      SUM(baseline_slot_delta)
        OVER (ORDER BY change_timestamp RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        AS baseline_slots,
    FROM
      reservation_slot_data
  ),

  /*
  The committed_slots change history. For example:
  +---------------------+------------------------+------------------+
  |  change_timestamp   | capacity_commitment_id | slot_count_delta |
  +---------------------+------------------------+------------------+
  | 2023-07-20 19:30:27 | 12954109101902401697   |              100 |
  | 2023-07-27 22:29:21 | 11445583810276646822   |              100 |
  | 2023-07-27 23:10:06 | 7341455530498381779    |              100 |
  */
  capacity_commitment_slot_data AS (
    SELECT
      change_timestamp,
      capacity_commitment_id,
      CASE
        WHEN action = "CREATE" OR action = "UPDATE"
          THEN
            IFNULL(
              IF(
                LAG(action)
                  OVER (
                    PARTITION BY capacity_commitment_id ORDER BY change_timestamp ASC, action ASC
                  )
                  IN UNNEST(['CREATE', 'UPDATE']),
                slot_count - LAG(slot_count)
                  OVER (
                    PARTITION BY capacity_commitment_id ORDER BY change_timestamp ASC, action ASC
                  ),
                slot_count),
              slot_count)
        ELSE
          IF(
            LAG(action)
              OVER (PARTITION BY capacity_commitment_id ORDER BY change_timestamp ASC, action ASC)
              IN UNNEST(['CREATE', 'UPDATE']),
            -1 * slot_count,
            0)
        END
        AS slot_count_delta
    FROM
      `{{project_region}}.INFORMATION_SCHEMA.CAPACITY_COMMITMENT_CHANGES_BY_PROJECT`
    WHERE
      state = "ACTIVE"
      AND UPPER(edition) = edition_to_check
      AND change_timestamp <= end_time
  ),

  /*
  The total_committed_slots history. For example:
  +---------------------+---------------+
  |  change_timestamp   | capacity_slot |
  +---------------------+---------------+
  | 2023-07-20 19:30:27 |           100 |
  | 2023-07-27 22:29:21 |           200 |
  | 2023-07-27 23:10:06 |           300 |
  */
  running_capacity_commitment_slot_data AS (
    SELECT
      change_timestamp,
      SUM(slot_count_delta)
        OVER (ORDER BY change_timestamp RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        AS capacity_slot
    FROM
      capacity_commitment_slot_data
  ),

  /* Add next_change_timestamp to the above data,
   which will be used when joining with reservation data. For example:
  +---------------------+-----------------------+---------------+
  |  change_timestamp   | next_change_timestamp | capacity_slot |
  +---------------------+-----------------------+---------------+
  | 2023-07-20 19:30:27 |   2023-07-27 22:29:21 |           100 |
  | 2023-07-27 22:29:21 |   2023-07-27 23:10:06 |           200 |
  | 2023-07-27 23:10:06 |   2023-07-31 00:14:37 |           300 |
  */
  running_capacity_commitment_slot_data_with_next_change AS (
    SELECT
      change_timestamp,
      IFNULL(LEAD(change_timestamp) OVER (ORDER BY change_timestamp ASC), CURRENT_TIMESTAMP())
        AS next_change_timestamp,
      capacity_slot
    FROM
      running_capacity_commitment_slot_data
  ),

  /*
  Whenever we have a change in reservations or commitments,
  the scaled_slots_and_baseline_not_covered_by_commitments will be changed.
  Hence we get a collection of all the change_timestamp from both tables.
  +---------------------+
  |  change_timestamp   |
  +---------------------+
  | 2023-07-20 19:30:27 |
  | 2023-07-27 22:24:15 |
  | 2023-07-27 22:25:21 |
  | 2023-07-27 22:29:21 |
  | 2023-07-27 22:39:14 |
  | 2023-07-27 22:40:20 |
  | 2023-07-27 22:54:18 |
  | 2023-07-27 22:55:23 |
  | 2023-07-27 23:10:06 |
  */
  merged_timestamp AS (
    SELECT
      change_timestamp
    FROM
      running_reservation_slot_data
    UNION DISTINCT
    SELECT
      change_timestamp
    FROM
      running_capacity_commitment_slot_data
  ),

  /*
  Change running reservation-slots and make sure we have one row when commitment changes.
  +---------------------+-------------------------+----------------+
  |  change_timestamp   | autoscale_current_slots | baseline_slots |
  +---------------------+-------------------------+----------------+
  | 2023-07-20 19:30:27 |                       0 |              0 |
  | 2023-07-27 22:24:15 |                       0 |            300 |
  | 2023-07-27 22:25:21 |                     180 |            300 |
  | 2023-07-27 22:29:21 |                     180 |            300 |
  | 2023-07-27 22:39:14 |                     100 |            300 |
  | 2023-07-27 22:40:20 |                     100 |            600 |
  | 2023-07-27 22:54:18 |                     220 |            600 |
  | 2023-07-27 22:55:23 |                     120 |            600 |
  | 2023-07-27 23:10:06 |                     120 |            600 |
  */
  running_reservation_slot_data_with_merged_timestamp AS (
    SELECT
      change_timestamp,
      IFNULL(
        autoscale_current_slots,
        IFNULL(
          LAST_VALUE(autoscale_current_slots IGNORE NULLS) OVER (ORDER BY change_timestamp ASC), 0))
        AS autoscale_current_slots,
      IFNULL(
        baseline_slots,
        IFNULL(LAST_VALUE(baseline_slots IGNORE NULLS) OVER (ORDER BY change_timestamp ASC), 0))
        AS baseline_slots
    FROM
      running_reservation_slot_data
    RIGHT JOIN
      merged_timestamp
      USING (change_timestamp)
  ),

  /*
  Join the above, so that we will know the number for baseline not covered by commitments.
  +---------------------+-----------------------+-------------------------+------------------------------------+
  |  change_timestamp   | next_change_timestamp | autoscale_current_slots | baseline_not_covered_by_commitment |
  +---------------------+-----------------------+-------------------------+------------------------------------+
  | 2023-07-20 19:30:27 |   2023-07-27 22:24:15 |                       0 |                                  0 |
  | 2023-07-27 22:24:15 |   2023-07-27 22:25:21 |                       0 |                                200 |
  | 2023-07-27 22:25:21 |   2023-07-27 22:29:21 |                     180 |                                200 |
  | 2023-07-27 22:29:21 |   2023-07-27 22:39:14 |                     180 |                                100 |
  | 2023-07-27 22:39:14 |   2023-07-27 22:40:20 |                     100 |                                100 |
  | 2023-07-27 22:40:20 |   2023-07-27 22:54:18 |                     100 |                                400 |
  | 2023-07-27 22:54:18 |   2023-07-27 22:55:23 |                     220 |                                400 |
  | 2023-07-27 22:55:23 |   2023-07-27 23:10:06 |                     120 |                                400 |
  | 2023-07-27 23:10:06 |   2023-07-31 00:16:07 |                     120 |                                300 |
  */
  scaled_slots_and_baseline_not_covered_by_commitments AS (
    SELECT
      r.change_timestamp,
      IFNULL(LEAD(r.change_timestamp) OVER (ORDER BY r.change_timestamp ASC), CURRENT_TIMESTAMP())
        AS next_change_timestamp,
      r.autoscale_current_slots,
      IF(
        r.baseline_slots - IFNULL(c.capacity_slot, 0) > 0,
        r.baseline_slots - IFNULL(c.capacity_slot, 0),
        0) AS baseline_not_covered_by_commitment
    FROM
      running_reservation_slot_data_with_merged_timestamp r
    LEFT JOIN
      running_capacity_commitment_slot_data_with_next_change c
      ON
        r.change_timestamp >= c.change_timestamp
        AND r.change_timestamp < c.next_change_timestamp
  ),

  /*
  The slot_seconds between each changes. For example:
  +---------------------+--------------------+
  |  change_timestamp   | slot_seconds |
  +---------------------+--------------+
  | 2023-07-20 19:30:27 |            0 |
  | 2023-07-27 22:24:15 |        13400 |
  | 2023-07-27 22:25:21 |        91580 |
  | 2023-07-27 22:29:21 |       166320 |
  | 2023-07-27 22:39:14 |        13200 |
  | 2023-07-27 22:40:20 |       419500 |
  | 2023-07-27 22:54:18 |        40920 |
  | 2023-07-27 22:55:23 |       459160 |
  | 2023-07-27 23:10:06 |     11841480 |
  */
  slot_seconds_data AS (
    SELECT
      change_timestamp,
      GetSlotSecondsBetweenChanges(
        autoscale_current_slots + baseline_not_covered_by_commitment,
        UNIX_MILLIS(change_timestamp),
        UNIX_MILLIS(next_change_timestamp),
        UNIX_MILLIS(start_time),
        UNIX_MILLIS(end_time)) AS slot_seconds
    FROM
      scaled_slots_and_baseline_not_covered_by_commitments
    WHERE
      change_timestamp <= end_time AND next_change_timestamp > start_time
  ),

/*
Final result for this example:
+--------------------+
| total_slot_seconds |
+--------------------+
|           13045560 |
*/
slot_seconds_cumulative AS 
(
SELECT
  metadatalevel as metadata_level,
  edition_to_check as edition,
  SUM(slot_seconds) AS total_slot_seconds
FROM
  slot_seconds_data
)

select * from slot_seconds_cumulative
WHERE total_slot_seconds IS NOT NULL