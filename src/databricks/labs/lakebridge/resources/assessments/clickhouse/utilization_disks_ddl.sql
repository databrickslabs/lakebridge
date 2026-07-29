CREATE TABLE IF NOT EXISTS utilization_disks (
    name VARCHAR,
    path VARCHAR,
    free_space UBIGINT,
    total_space UBIGINT,
    keep_free_space BIGINT,
    "type" VARCHAR
);
