CREATE TABLE IF NOT EXISTS aggregate_rules (
    rule_id BIGINT NOT NULL COMMENT 'Aggregate rule id; referenced by aggregate_metrics and aggregate_details.',
    rule_type STRING NOT NULL COMMENT 'Rule category, e.g. AGGREGATE.',
    rule_info MAP<STRING, STRING> NOT NULL COMMENT 'Rule definition: agg_type, agg_column, group_by_columns.',
    inserted_ts TIMESTAMP NOT NULL COMMENT 'Row insert timestamp.'
)
COMMENT 'Definitions of aggregate rules (one row per rule). Referenced by aggregate_metrics/aggregate_details on rule_id.';
