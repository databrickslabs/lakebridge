CREATE TABLE IF NOT EXISTS utilization_mutations (
    database VARCHAR,
    "table" VARCHAR,
    mutation_id VARCHAR,
    command VARCHAR,
    create_time TIMESTAMP,
    is_done BIGINT,
    parts_to_do_names VARCHAR,
    parts_to_do BIGINT,
    latest_fail_reason VARCHAR
);
