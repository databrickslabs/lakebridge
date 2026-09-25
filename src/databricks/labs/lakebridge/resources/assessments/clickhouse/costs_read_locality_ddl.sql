CREATE TABLE IF NOT EXISTS costs_read_locality (
    normalized_query_hash UBIGINT,
    runs UBIGINT,
    cache_read_bytes UBIGINT,
    s3_source_read_bytes UBIGINT,
    s3_read_bytes UBIGINT,
    s3_read_requests UBIGINT,
    cache_hit_pct DOUBLE
);
