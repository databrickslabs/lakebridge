CREATE TABLE spark_application_attempts (
    application_id VARCHAR,
    attempt_id VARCHAR,
    started_time TIMESTAMP,
    ended_time TIMESTAMP,
    last_updated_time TIMESTAMP,
    duration_ms BIGINT,
    spark_user VARCHAR,
    spark_version VARCHAR,
    completed BOOLEAN
)
