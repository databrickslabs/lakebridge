CREATE TABLE IF NOT EXISTS metrics (
    recon_table_id BIGINT NOT NULL COMMENT 'Join key to main (one reconciled table per run).',
    recon_metrics STRUCT<
                        source_record_count: BIGINT COMMENT 'Total rows read from source.',
                        target_record_count: BIGINT COMMENT 'Total rows read from target.',
                        row_comparison: STRUCT<
                                                missing_in_source: BIGINT COMMENT 'Rows present only in target.',
                                                missing_in_target: BIGINT COMMENT 'Rows present only in source.'
                                              > COMMENT 'Row presence diffs.',
                        column_comparison: STRUCT<
                                                   absolute_mismatch: BIGINT COMMENT 'Rows mismatching outside thresholds.',
                                                   threshold_mismatch: BIGINT COMMENT 'Rows mismatching within configured thresholds.',
                                                   mismatch_columns: STRING COMMENT 'Comma-separated mismatching column names.'
                                                 > COMMENT 'Column-value diffs.',
                        schema_comparison: BOOLEAN COMMENT 'True if schema matched.'
                    > COMMENT 'Authoritative totals for the comparison (details is only a sample).',
    run_metrics STRUCT<
                        status: BOOLEAN NOT NULL COMMENT 'True = pass. False if mismatch outside thresholds, any missing rows, or invalid schema.',
                        run_by_user: STRING NOT NULL COMMENT 'User that ran the reconcile.',
                        exception_message: STRING COMMENT 'Non-empty means the run errored (vs merely found diffs).'
                       > NOT NULL COMMENT 'Run verdict and execution status.',
    inserted_ts TIMESTAMP NOT NULL COMMENT 'Row insert timestamp.'
)
COMMENT 'Authoritative totals and pass/fail verdict per reconciled table. One row per recon_table_id. Join to main on recon_table_id.';
