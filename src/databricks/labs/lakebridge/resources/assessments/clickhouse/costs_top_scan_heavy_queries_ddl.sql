CREATE TABLE IF NOT EXISTS costs_top_scan_heavy_queries (
    normalized_query_hash UBIGINT,
    runs UBIGINT,
    total_read_bytes UBIGINT,
    avg_read_bytes DOUBLE,
    total_read_rows UBIGINT,
    total_result_rows UBIGINT,
    p95_ms DOUBLE,
    p99_ms DOUBLE,
    sample_query VARCHAR,
    scan_efficiency_pct DOUBLE
);
