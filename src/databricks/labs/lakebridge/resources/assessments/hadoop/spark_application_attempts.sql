-- One row per Spark application attempt. A running attempt has no end time (Spark reports -1). The times come from the
-- epoch fields or, when the server reports only text, from the text times. The duration is the server's, NULL when the
-- server does not report one, as in the original profiler's output.
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
    epoch_ms(spark_time_ms(att, 'startTime')) AS started_time,
    epoch_ms(spark_time_ms(att, 'endTime')) AS ended_time,
    epoch_ms(spark_time_ms(att, 'lastUpdated')) AS last_updated_time,
    CAST(att->>'$.duration' AS BIGINT) AS duration_ms,
    att->>'$.sparkUser' AS spark_user,
    att->>'$.appSparkVersion' AS spark_version,
    CAST(att->>'$.completed' AS BOOLEAN) AS completed
FROM attempts
