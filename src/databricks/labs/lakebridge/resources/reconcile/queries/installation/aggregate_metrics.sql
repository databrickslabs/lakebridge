CREATE TABLE IF NOT EXISTS aggregate_metrics (
    recon_table_id BIGINT NOT NULL,
    rule_id BIGINT NOT NULL,
    recon_metrics STRUCT<
                                                missing_in_source: INTEGER,
                                                missing_in_target: INTEGER,
                                                mismatch: INTEGER
                                           >,
    run_metrics STRUCT<
                        status: BOOLEAN NOT NULL,
                        run_by_user: STRING NOT NULL,
                        exception_message: STRING
                       > NOT NULL,
    inserted_ts TIMESTAMP NOT NULL
);
