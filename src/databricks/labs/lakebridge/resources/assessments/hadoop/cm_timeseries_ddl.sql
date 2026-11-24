CREATE TABLE cm_timeseries (
    query_name VARCHAR,
    status VARCHAR,
    metric_name VARCHAR,
    entity_name VARCHAR,
    cluster_name VARCHAR,
    entity_attributes VARCHAR,
    sample_time TIMESTAMP,
    sample_value DOUBLE,
    rollup VARCHAR,
    unit VARCHAR
)
