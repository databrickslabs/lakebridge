CREATE TABLE IF NOT EXISTS features_view_refreshes (
    database VARCHAR,
    view VARCHAR,
    uuid UUID,
    status VARCHAR,
    last_success_time TIMESTAMP,
    last_success_duration_ms BIGINT,
    last_refresh_time TIMESTAMP,
    last_refresh_replica VARCHAR,
    next_refresh_time TIMESTAMP,
    exception VARCHAR,
    retry BIGINT,
    progress DOUBLE,
    read_rows BIGINT,
    read_bytes BIGINT,
    total_rows BIGINT,
    written_rows BIGINT,
    written_bytes BIGINT
);
