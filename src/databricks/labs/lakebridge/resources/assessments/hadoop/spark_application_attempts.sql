-- One row per Spark application attempt. A running attempt has no end time (Spark reports endTimeEpoch -1).
WITH apps AS (
    SELECT t.a AS a
    FROM (SELECT unnest(hadoop_records(doc, '$')) AS a FROM raw_documents) AS t
),
attempts AS (
    SELECT apps.a->>'$.id' AS application_id, u.att AS att
    FROM apps, UNNEST(hadoop_records(apps.a, '$.attempts')) AS u(att)
)
SELECT
    application_id,
    att->>'$.attemptId' AS attempt_id,
    epoch_ms(CAST(att->>'$.startTimeEpoch' AS BIGINT)) AS started_time,
    epoch_ms(NULLIF(CAST(att->>'$.endTimeEpoch' AS BIGINT), -1)) AS ended_time,
    epoch_ms(NULLIF(CAST(att->>'$.lastUpdatedEpoch' AS BIGINT), -1)) AS last_updated_time,
    CAST(att->>'$.duration' AS BIGINT) AS duration_ms,
    att->>'$.sparkUser' AS spark_user,
    att->>'$.appSparkVersion' AS spark_version,
    CAST(att->>'$.completed' AS BOOLEAN) AS completed
FROM attempts
