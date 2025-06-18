CREATE TABLE IF NOT EXISTS aggregate_rules (
    rule_id BIGINT NOT NULL,
    rule_type STRING NOT NULL,
    rule_info MAP<STRING, STRING> NOT NULL,
    inserted_ts TIMESTAMP NOT NULL
);
