CREATE TABLE IF NOT EXISTS features_view_refreshes (
    database VARCHAR,
    view VARCHAR,
    uuid UUID,
    status VARCHAR,
    last_success_time TIMESTAMP,
    last_success_duration_ms UBIGINT,
    last_refresh_time TIMESTAMP,
    last_refresh_replica VARCHAR,
    next_refresh_time TIMESTAMP,
    exception VARCHAR,
    retry UBIGINT,
    progress DOUBLE,
    read_rows UBIGINT,
    read_bytes UBIGINT,
    total_rows UBIGINT,
    written_rows UBIGINT,
    written_bytes UBIGINT
);
