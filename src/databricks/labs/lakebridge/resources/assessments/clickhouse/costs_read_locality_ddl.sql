CREATE TABLE IF NOT EXISTS costs_read_locality (
    normalized_query_hash UBIGINT,
    runs BIGINT,
    cache_read_bytes BIGINT,
    s3_source_read_bytes BIGINT,
    s3_read_bytes BIGINT,
    s3_read_requests BIGINT,
    cache_hit_pct DOUBLE
);
