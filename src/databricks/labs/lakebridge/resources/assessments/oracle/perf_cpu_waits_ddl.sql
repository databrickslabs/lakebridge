CREATE TABLE perf_cpu_waits (
    pdb_name VARCHAR,
    instance_number INTEGER,
    mtime TIMESTAMP,
    event VARCHAR,
    wait_class VARCHAR,
    total_wait_time BIGINT
);
