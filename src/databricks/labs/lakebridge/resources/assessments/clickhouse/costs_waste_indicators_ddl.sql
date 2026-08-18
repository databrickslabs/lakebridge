CREATE TABLE IF NOT EXISTS costs_waste_indicators (
    normalized_query_hash UBIGINT,
    sample_user VARCHAR,
    runs UBIGINT,
    total_read_bytes UBIGINT,
    total_read_rows UBIGINT,
    total_result_rows UBIGINT,
    scan_efficiency_pct DOUBLE,
    compute_weight UBIGINT,
    sample_query VARCHAR
);
