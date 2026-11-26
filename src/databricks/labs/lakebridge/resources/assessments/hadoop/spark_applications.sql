-- One row per Spark History Server application; the name is kept as reported, as the original does.
WITH apps AS (
    SELECT t.a AS a
    FROM (SELECT unnest(hadoop_records(doc, '$')) AS a FROM raw_documents) AS t
),
attempts AS (
    SELECT apps.a->>'$.id' AS application_id, u.att AS att
    FROM apps, UNNEST(hadoop_records(apps.a, '$.attempts')) AS u(att)
),
summary AS (
    SELECT
        application_id,
        COUNT(*) AS attempt_count,
        ANY_VALUE(att->>'$.sparkUser') AS spark_user,
        ANY_VALUE(att->>'$.appSparkVersion') AS spark_version,
        MIN(spark_time_ms(att, 'startTime')) AS first_start_ms,
        MAX(spark_time_ms(att, 'endTime')) AS last_end_ms,
        BOOL_AND(CAST(att->>'$.completed' AS BOOLEAN)) AS completed
    FROM attempts
    GROUP BY application_id
)
SELECT
    apps.a->>'$.id' AS application_id,
    NULLIF(apps.a->>'$.name', '') AS name,
    COALESCE(summary.attempt_count, 0) AS attempt_count,
    summary.spark_user AS spark_user,
    summary.spark_version AS spark_version,
    epoch_ms(summary.first_start_ms) AS started_time,
    epoch_ms(summary.last_end_ms) AS ended_time,
    summary.completed AS completed
FROM apps
LEFT JOIN summary ON summary.application_id = (apps.a->>'$.id')
