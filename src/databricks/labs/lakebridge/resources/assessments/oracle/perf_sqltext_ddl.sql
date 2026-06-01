CREATE TABLE perf_sqltext (
    dt TIMESTAMP,
    pdb_name VARCHAR,
    command VARCHAR,
    parsing_schema_name VARCHAR,
    instance_number INTEGER,
    cnt INTEGER,
    total_run_time_secs DOUBLE
);
