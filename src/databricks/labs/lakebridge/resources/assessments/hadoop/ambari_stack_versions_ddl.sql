CREATE TABLE ambari_stack_versions (
    cluster_name VARCHAR,
    id BIGINT,
    stack VARCHAR,
    version VARCHAR,
    repository_version BIGINT,
    state VARCHAR,
    supports_revert BOOLEAN,
    host_states VARCHAR,
    repository_versions VARCHAR
)
