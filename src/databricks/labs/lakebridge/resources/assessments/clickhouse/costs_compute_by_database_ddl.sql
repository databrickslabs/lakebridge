CREATE TABLE IF NOT EXISTS costs_compute_by_database (
    db VARCHAR,
    runs BIGINT,
    compute_weight BIGINT,
    total_duration_ms BIGINT,
    total_read_bytes BIGINT,
    total_written_bytes BIGINT,
    compute_weight_pct DOUBLE
);
