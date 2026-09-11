-- Error/exception analysis (query_log). sample_exception redacted (default-on): the
-- exception text can echo literal predicate values / PII from the failing query.
SELECT
    exception_code,
    '[REDACTED]' AS sample_exception,
    count() AS occurrences,
    uniqExact(user) AS affected_users,
    uniqExact(normalized_query_hash) AS distinct_queries,
    min(event_time) AS first_seen,
    max(event_time) AS last_seen
FROM clusterAllReplicas('default', system.query_log)
WHERE type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing')
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY exception_code
ORDER BY occurrences DESC
LIMIT 50
