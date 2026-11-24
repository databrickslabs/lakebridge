-- Impala daemon web UI: one row per query each coordinator knows, completed (its query log) or in flight.
-- Each daemon only lists the queries it coordinated, so the rows of every listed daemon are unioned. A query that is
-- in both lists of one daemon keeps its completed row. The durations are in milliseconds, the sizes in bytes; the
-- timestamps are local times as Impala prints them. query_json keeps the whole record as Impala returned it.
-- Impala's completed-query log holds only the last query_log_size queries (200 by default) for each daemon.
WITH queries AS (
    SELECT doc->>'$.coordinator' AS coordinator_url, t.q AS q, FALSE AS is_in_flight
    FROM raw_documents, UNNEST(hadoop_records(doc, '$.body.completed_queries')) AS t(q)
    UNION ALL
    SELECT doc->>'$.coordinator' AS coordinator_url, t.q AS q, TRUE AS is_in_flight
    FROM raw_documents, UNNEST(hadoop_records(doc, '$.body.in_flight_queries')) AS t(q)
)
SELECT
    q->>'$.query_id' AS query_id,
    coordinator_url,
    regexp_extract(coordinator_url, '^[a-zA-Z]+://([^/:]+)', 1) AS coordinator_host,
    is_in_flight,
    q->>'$.stmt' AS statement,
    q->>'$.stmt_type' AS stmt_type,
    q->>'$.effective_user' AS user_name,
    q->>'$.default_db' AS default_db,
    q->>'$.state' AS state,
    impala_timestamp(q->>'$.start_time') AS start_time,
    impala_timestamp(q->>'$.end_time') AS end_time,
    impala_duration_ms(q->>'$.duration') AS duration_ms,
    impala_duration_ms(q->>'$.queued_duration') AS queued_ms,
    impala_duration_ms(q->>'$.waiting_time') AS waiting_ms,
    impala_bytes(q->>'$.mem_usage') AS mem_usage_bytes,
    impala_bytes(q->>'$.mem_est') AS mem_est_bytes,
    TRY_CAST(q->>'$.rows_fetched' AS BIGINT) AS rows_fetched,
    impala_bytes(q->>'$.bytes_read') AS bytes_read,
    impala_bytes(q->>'$.bytes_sent') AS bytes_sent,
    NULLIF(q->>'$.resource_pool', '') AS resource_pool,
    q->>'$.progress' AS progress,
    CAST(q AS VARCHAR) AS query_json
FROM queries
QUALIFY ROW_NUMBER() OVER (PARTITION BY q->>'$.query_id' ORDER BY is_in_flight) = 1
