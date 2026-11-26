CREATE TABLE impala_statestore (
    statestore_url VARCHAR,
    subscriber_id VARCHAR,
    address VARCHAR,
    num_topics BIGINT,
    num_priority_topics BIGINT,
    num_transient BIGINT,
    registration_id VARCHAR,
    secs_since_heartbeat DOUBLE
)
