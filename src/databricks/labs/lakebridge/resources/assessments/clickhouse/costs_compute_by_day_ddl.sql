CREATE TABLE IF NOT EXISTS costs_compute_by_day (
    day DATE,
    runs BIGINT,
    compute_weight BIGINT,
    total_read_bytes BIGINT,
    total_written_bytes BIGINT
);
