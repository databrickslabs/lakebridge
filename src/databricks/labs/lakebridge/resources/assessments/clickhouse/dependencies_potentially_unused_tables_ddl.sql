CREATE TABLE IF NOT EXISTS dependencies_potentially_unused_tables (
    database VARCHAR,
    name VARCHAR,
    engine VARCHAR,
    total_rows UBIGINT,
    total_bytes UBIGINT,
    size_readable VARCHAR
);
