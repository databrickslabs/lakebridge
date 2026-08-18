CREATE TABLE IF NOT EXISTS costs_compute_by_database (
    db VARCHAR,
    runs UBIGINT,
    compute_weight UBIGINT,
    total_duration_ms UBIGINT,
    total_read_bytes UBIGINT,
    total_written_bytes UBIGINT,
    compute_weight_pct DOUBLE
);
