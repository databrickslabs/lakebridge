CREATE TABLE IF NOT EXISTS costs_compute_by_day (
    day DATE,
    runs UBIGINT,
    compute_weight UBIGINT,
    total_read_bytes UBIGINT,
    total_written_bytes UBIGINT
);
