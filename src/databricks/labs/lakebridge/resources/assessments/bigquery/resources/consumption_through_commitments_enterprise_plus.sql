-- This script shows billed slot seconds covered by a commitment_plan.
-- It needs to run in the same project the reservations were created in. 
-- Editions options are STANDARD, ENTERPRISE, and ENTERPRISE PLUS.

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
SET edition_to_check = 'ENTERPRISE PLUS';
SET metadatalevel = '{{project_region}}';

/* The following function returns the slot seconds for the time window between
two capacity changes. For example, if there are 100 slots between (2023-06-01
10:00:00, 2023-06-01 11:00:00), then during that window the total slot seconds
will be 100 * 3600.

This script calculates a specific window (based on the variables defined above),
which is why the following script includes script_start_timestamp_unix_millis
and script_end_timestamp_unix_millis. */

CREATE TEMP FUNCTION
GetSlotSecondsBetweenChanges(
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
Sample CAPACITY_COMMITMENT_CHANGES data (unrelated columns ignored):
+---------------------+------------------------+-----------------+--------+------------+--------+
|  change_timestamp   | capacity_commitment_id | commitment_plan | state  | slot_count | action |
+---------------------+------------------------+-----------------+--------+------------+--------+
| 2023-07-20 19:30:27 | 12954109101902401697   | ANNUAL          | ACTIVE |        100 | CREATE |
| 2023-07-27 22:29:21 | 11445583810276646822   | FLEX            | ACTIVE |        100 | CREATE |
| 2023-07-27 23:10:06 | 7341455530498381779    | MONTHLY         | ACTIVE |        100 | CREATE |
| 2023-07-27 23:11:06 | 7341455530498381779    | FLEX            | ACTIVE |        100 | UPDATE |

The last row indicates a special change from MONTHLY to FLEX, which happens
because of commercial migration.

*/

WITH
  /*
  Information containing which commitment might have plan
  updated (e.g. renewal or commercial migration). For example:
  +------------------------+------------------+--------------------+--------+------------+--------+-----------+----------------------------+
  |  change_timestamp   | capacity_commitment_id | commitment_plan | state  | slot_count | action | next_plan | next_plan_change_timestamp |
  +---------------------+------------------------+-----------------+--------+------------+--------+-----------+----------------------------+
  | 2023-07-20 19:30:27 | 12954109101902401697   | ANNUAL          | ACTIVE |        100 | CREATE | ANNUAL    |        2023-07-20 19:30:27 |
  | 2023-07-27 22:29:21 | 11445583810276646822   | FLEX            | ACTIVE |        100 | CREATE | FLEX      |        2023-07-27 22:29:21 |
  | 2023-07-27 23:10:06 | 7341455530498381779    | MONTHLY         | ACTIVE |        100 | CREATE | FLEX      |        2023-07-27 23:11:06 |
  | 2023-07-27 23:11:06 | 7341455530498381779    | FLEX            | ACTIVE |        100 | UPDATE | FLEX      |        2023-07-27 23:11:06 |
  */
  commitments_with_next_plan AS (
    SELECT
      *,
      IFNULL(
        LEAD(commitment_plan)
          OVER (
            PARTITION BY capacity_commitment_id ORDER BY change_timestamp ASC
          ),
        commitment_plan)
        next_plan,
      IFNULL(
        LEAD(change_timestamp)
          OVER (
            PARTITION BY capacity_commitment_id ORDER BY change_timestamp ASC
          ),
        change_timestamp)
        next_plan_change_timestamp
    FROM
      `{{project_region}}.INFORMATION_SCHEMA.CAPACITY_COMMITMENT_CHANGES_BY_PROJECT`
  ),

  /*
  Insert a 'DELETE' action for those with updated plans. The FLEX commitment
  '7341455530498381779' is has no 'CREATE' action, and is instead labeled as an
  'UPDATE' action.

  For example:
  +---------------------+------------------------+-----------------+--------+------------+--------+
  |  change_timestamp   | capacity_commitment_id | commitment_plan | state  | slot_count | action |
  +---------------------+------------------------+-----------------+--------+------------+--------+
  | 2023-07-20 19:30:27 | 12954109101902401697   | ANNUAL          | ACTIVE |        100 | CREATE |
  | 2023-07-27 22:29:21 | 11445583810276646822   | FLEX            | ACTIVE |        100 | CREATE |
  | 2023-07-27 23:10:06 | 7341455530498381779    | MONTHLY         | ACTIVE |        100 | CREATE |
  | 2023-07-27 23:11:06 | 7341455530498381779    | FLEX            | ACTIVE |        100 | UPDATE |
  | 2023-07-27 23:11:06 | 7341455530498381779    | MONTHLY         | ACTIVE |        100 | DELETE |
  */

  capacity_changes_with_additional_deleted_event_for_changed_plan AS (
    SELECT
      next_plan_change_timestamp AS change_timestamp,
      project_id,
      project_number,
      capacity_commitment_id,
      commitment_plan,
      state,
      slot_count,
      'DELETE' AS action,
      commitment_start_time,
      commitment_end_time,
      failure_status,
      renewal_plan,
      user_email,
      edition,
      is_flat_rate,
    FROM commitments_with_next_plan
    WHERE commitment_plan <> next_plan
    UNION ALL
    SELECT * FROM `{{project_region}}.INFORMATION_SCHEMA.CAPACITY_COMMITMENT_CHANGES_BY_PROJECT`
  ),

  /*
  The committed_slots change the history. For example:
  +---------------------+------------------------+------------------+-----------------+
  |  change_timestamp   | capacity_commitment_id | slot_count_delta | commitment_plan |
  +---------------------+------------------------+------------------+-----------------+
  | 2023-07-20 19:30:27 | 12954109101902401697   |              100 | ANNUAL          |
  | 2023-07-27 22:29:21 | 11445583810276646822   |              100 | FLEX            |
  | 2023-07-27 23:10:06 | 7341455530498381779    |              100 | MONTHLY         |
  | 2023-07-27 23:11:06 | 7341455530498381779    |             -100 | MONTHLY         |
  | 2023-07-27 23:11:06 | 7341455530498381779    |              100 | FLEX            |
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
        AS slot_count_delta,
      commitment_plan
    FROM
      capacity_changes_with_additional_deleted_event_for_changed_plan
    WHERE
      state = "ACTIVE"
      AND UPPER(edition) = edition_to_check
      AND change_timestamp <= end_time
  ),

  /*
  The total_committed_slots history for each plan. For example:
  +---------------------+---------------+-----------------+
  |  change_timestamp   | capacity_slot | commitment_plan |
  +---------------------+---------------+-----------------+
  | 2023-07-20 19:30:27 |           100 | ANNUAL          |
  | 2023-07-27 22:29:21 |           100 | FLEX            |
  | 2023-07-27 23:10:06 |           100 | MONTHLY         |
  | 2023-07-27 23:11:06 |             0 | MONTHLY         |
  | 2023-07-27 23:11:06 |           200 | FLEX            |
  */

  running_capacity_commitment_slot_data AS (
    SELECT
      change_timestamp,
      SUM(slot_count_delta)
        OVER (
          PARTITION BY commitment_plan
          ORDER BY change_timestamp
          RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )
        AS capacity_slot,
      commitment_plan,
    FROM
      capacity_commitment_slot_data
  ),

  /*

  The slot_seconds between each changes, partitioned by each plan. For example:
  +---------------------+--------------+-----------------+
  |  change_timestamp   | slot_seconds | commitment_plan |
  +---------------------+--------------+-----------------+
  | 2023-07-20 19:30:27 |     64617300 | ANNUAL          |
  | 2023-07-27 22:29:21 |       250500 | FLEX            |
  | 2023-07-27 23:10:06 |         6000 | MONTHLY         |
  | 2023-07-27 23:11:06 |            0 | MONTHLY         |
  | 2023-07-27 23:11:06 |      5626800 | FLEX            |
  */

  slot_seconds_data AS (
    SELECT
      change_timestamp,
      GetSlotSecondsBetweenChanges(
        capacity_slot,
        UNIX_MILLIS(change_timestamp),
        UNIX_MILLIS(
          IFNULL(
            LEAD(change_timestamp)
              OVER (PARTITION BY commitment_plan ORDER BY change_timestamp ASC),
            CURRENT_TIMESTAMP())),
        UNIX_MILLIS(start_time),
        UNIX_MILLIS(end_time)) AS slot_seconds,
      commitment_plan,
    FROM
      running_capacity_commitment_slot_data
    WHERE
      change_timestamp <= end_time
  )

/*

The final result is similar to the following:
+-----------------+--------------------+
| commitment_plan | total_slot_seconds |
+-----------------+--------------------+
| ANNUAL          |           64617300 |
| MONTHLY         |               6000 |
| FLEX            |            5877300 |
*/

SELECT
  metadatalevel as metadata_level,
  edition_to_check as edition,
  commitment_plan,
  SUM(slot_seconds) AS total_slot_seconds
FROM
  slot_seconds_data
GROUP BY
  commitment_plan