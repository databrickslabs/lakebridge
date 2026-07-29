CREATE TABLE IF NOT EXISTS costs_waste_indicators (
    normalized_query_hash UBIGINT,
    sample_user VARCHAR,
    runs BIGINT,
    total_read_bytes BIGINT,
    total_read_rows BIGINT,
    total_result_rows BIGINT,
    scan_efficiency_pct DOUBLE,
    compute_weight BIGINT,
    sample_query VARCHAR
);
