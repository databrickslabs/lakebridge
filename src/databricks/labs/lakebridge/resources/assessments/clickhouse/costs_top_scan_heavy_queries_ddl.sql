CREATE TABLE IF NOT EXISTS costs_top_scan_heavy_queries (
    normalized_query_hash UBIGINT,
    runs BIGINT,
    total_read_bytes BIGINT,
    avg_read_bytes DOUBLE,
    total_read_rows BIGINT,
    total_result_rows BIGINT,
    p95_ms DOUBLE,
    p99_ms DOUBLE,
    sample_query VARCHAR,
    scan_efficiency_pct DOUBLE
);
