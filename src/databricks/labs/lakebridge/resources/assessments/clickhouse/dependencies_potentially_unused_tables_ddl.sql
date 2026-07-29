CREATE TABLE IF NOT EXISTS dependencies_potentially_unused_tables (
    database VARCHAR,
    name VARCHAR,
    engine VARCHAR,
    total_rows BIGINT,
    total_bytes BIGINT,
    size_readable VARCHAR
);
