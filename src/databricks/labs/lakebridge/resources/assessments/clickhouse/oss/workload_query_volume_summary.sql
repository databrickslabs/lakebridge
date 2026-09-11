-- Query volume, success/error rates, and I/O by query kind (query_log).
SELECT
    query_kind,
    count() AS total_queries,
    countIf(type = 'QueryFinish') AS succeeded,
    countIf(type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing')) AS failed,
    round(countIf(type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing')) /
          nullIf(count(), 0) * 100, 2) AS error_rate_pct,
    uniqExact(user) AS distinct_users,
    uniqExact(normalized_query_hash) AS distinct_query_shapes,
    sum(read_bytes) AS total_read_bytes,
    sum(written_bytes) AS total_written_bytes,
    sum(read_rows) AS total_read_rows,
    sum(written_rows) AS total_written_rows,
    sum(result_rows) AS total_result_rows
FROM system.query_log
WHERE event_time >= now() - INTERVAL 30 DAY
  AND is_initial_query = 1
GROUP BY query_kind
ORDER BY total_queries DESC
