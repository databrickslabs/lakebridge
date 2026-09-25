CREATE TABLE IF NOT EXISTS objects_dictionaries (
    database VARCHAR,
    name VARCHAR,
    status VARCHAR,
    origin VARCHAR,
    "type" VARCHAR,
    key_names VARCHAR,
    key_types VARCHAR,
    attr_names VARCHAR,
    attr_types VARCHAR,
    element_count UBIGINT,
    bytes_allocated UBIGINT,
    loading_duration DOUBLE,
    last_successful_update_time TIMESTAMP,
    loading_start_time TIMESTAMP,
    source VARCHAR
);
