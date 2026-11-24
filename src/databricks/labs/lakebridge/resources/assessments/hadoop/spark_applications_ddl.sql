CREATE TABLE spark_applications (
    application_id VARCHAR,
    name VARCHAR,
    attempt_count BIGINT,
    spark_user VARCHAR,
    spark_version VARCHAR,
    started_time TIMESTAMP,
    ended_time TIMESTAMP,
    completed BOOLEAN
)
