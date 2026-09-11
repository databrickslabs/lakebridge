-- Read locality per normalized query (query_log ProfileEvents): bytes served from the
-- local filesystem cache vs fetched from the object-storage source (S3). On ClickHouse
-- Cloud (SharedMergeTree) all data is on object storage, so these counters reflect how
-- well the local cache is absorbing reads. cache_hit_pct = cache / (cache + source) — the
-- share of cache-eligible bytes that avoided an object-storage read. A low value flags an
-- S3-read-heavy workload. Aggregated across replicas.
SELECT
    normalized_query_hash,
    count() AS runs,
    sum(ProfileEvents['CachedReadBufferReadFromCacheBytes']) AS cache_read_bytes,
    sum(ProfileEvents['CachedReadBufferReadFromSourceBytes']) AS s3_source_read_bytes,
    sum(ProfileEvents['ReadBufferFromS3Bytes']) AS s3_read_bytes,
    sum(ProfileEvents['S3ReadRequestsCount']) AS s3_read_requests,
    round(
        sum(ProfileEvents['CachedReadBufferReadFromCacheBytes'])
        / greatest(
            sum(ProfileEvents['CachedReadBufferReadFromCacheBytes'])
            + sum(ProfileEvents['CachedReadBufferReadFromSourceBytes']),
            1
        ) * 100,
        2
    ) AS cache_hit_pct
FROM clusterAllReplicas('default', system.query_log)
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY normalized_query_hash
HAVING cache_read_bytes + s3_source_read_bytes > 0
ORDER BY s3_source_read_bytes DESC
LIMIT 30
