CREATE TABLE IF NOT EXISTS aggregate_metrics (
    recon_table_id BIGINT NOT NULL COMMENT 'Join key to main.',
    rule_id BIGINT NOT NULL COMMENT 'Join key to aggregate_rules (which aggregate this measures).',
    recon_metrics STRUCT<
                        missing_in_source: INTEGER COMMENT 'Aggregate groups present only in target.',
                        missing_in_target: INTEGER COMMENT 'Aggregate groups present only in source.',
                        mismatch: INTEGER COMMENT 'Aggregate groups whose value differs.'
                   > COMMENT 'Per-rule aggregate comparison totals.',
    run_metrics STRUCT<
                        status: BOOLEAN NOT NULL COMMENT 'True = pass.',
                        run_by_user: STRING NOT NULL COMMENT 'User that ran the reconcile.',
                        exception_message: STRING COMMENT 'Non-empty means the run errored.'
                       > NOT NULL COMMENT 'Run verdict and execution status.',
    inserted_ts TIMESTAMP NOT NULL COMMENT 'Row insert timestamp.'
)
COMMENT 'Aggregates-reconcile results per (table, rule). Join main on recon_table_id, aggregate_rules on rule_id.';
