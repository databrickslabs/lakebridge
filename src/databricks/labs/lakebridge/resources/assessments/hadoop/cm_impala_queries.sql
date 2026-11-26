-- Cloudera Manager: one row per Impala query; attributes keeps every attribute as JSON text. Statements and users are
-- kept as CM returns them.
SELECT
    q->>'$.queryId' AS query_id,
    q->>'$.queryType' AS query_type,
    q->>'$.queryState' AS query_state,
    q->>'$.statement' AS statement,
    q->>'$.database' AS database_name,
    q->>'$.user' AS user_name,
    q->>'$.coordinator.hostId' AS coordinator_host,
    TRY_CAST(q->>'$.startTime' AS TIMESTAMP) AS start_time,
    TRY_CAST(q->>'$.endTime' AS TIMESTAMP) AS end_time,
    TRY_CAST(q->>'$.durationMillis' AS BIGINT) AS duration_ms,
    TRY_CAST(q->>'$.rowsProduced' AS BIGINT) AS rows_produced,
    TRY_CAST(q->>'$.detailsAvailable' AS BOOLEAN) AS details_available,
    q->>'$.attributes.pool' AS pool_name,
    q->>'$.attributes.query_status' AS query_status,
    q->>'$.attributes.admission_result' AS admission_result,
    TRY_CAST(q->>'$.attributes.oom' AS BOOLEAN) AS oom,
    TRY_CAST(q->>'$.attributes.memory_per_node_peak' AS BIGINT) AS memory_per_node_peak_bytes,
    TRY_CAST(q->>'$.attributes.hdfs_bytes_read' AS BIGINT) AS hdfs_bytes_read,
    TRY_CAST(q->>'$.attributes.memory_spilled' AS BIGINT) AS memory_spilled_bytes,
    TRY_CAST(q->>'$.attributes.cm_cpu_milliseconds' AS DOUBLE) AS cm_cpu_milliseconds,
    CAST(q->'$.attributes' AS VARCHAR) AS attributes
FROM (SELECT unnest(hadoop_records(doc, '$.queries')) AS q FROM raw_documents) AS t
