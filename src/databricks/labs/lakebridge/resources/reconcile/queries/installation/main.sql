CREATE TABLE IF NOT EXISTS main (
    recon_table_id BIGINT NOT NULL COMMENT 'Per-(run, table) id = hash(recon_id, source_table, target_table). Join key to metrics, details, schema_details.',
    recon_id STRING NOT NULL COMMENT 'Reconcile run id (one CLI invocation). Join key to recon_run_context.',
    source_type STRING NOT NULL COMMENT 'Source dialect: Snowflake | Oracle | Databricks | MSSQL | Synapse | Redshift | Teradata.',
    source_table STRUCT<
                         catalog: STRING COMMENT 'Source catalog; may be null (e.g. 2-level namespaces).',
                         schema: STRING NOT NULL COMMENT 'Source schema.',
                         table_name: STRING NOT NULL COMMENT 'Source table name.'
                        > COMMENT 'Fully-qualified source table identity.',
    target_table STRUCT<
                         catalog: STRING NOT NULL COMMENT 'Databricks target catalog.',
                         schema: STRING NOT NULL COMMENT 'Databricks target schema.',
                         table_name: STRING NOT NULL COMMENT 'Databricks target table name.'
                        > NOT NULL COMMENT 'Fully-qualified Databricks target table identity.',
    report_type STRING NOT NULL COMMENT 'Checks run: schema | row | data | all. Gates which metrics columns are populated.',
    operation_name  STRING NOT NULL COMMENT 'reconcile | aggregates-reconcile.',
    start_ts TIMESTAMP COMMENT 'Run start timestamp.',
    end_ts TIMESTAMP COMMENT 'Run end timestamp.'
)
COMMENT 'Header/identity row per reconciled table per run. Join metrics/details/schema_details on recon_table_id; recon_run_context on recon_id.'
TBLPROPERTIES (
    'delta.columnMapping.mode' = 'name',
    'delta.minReaderVersion' = '2',
    'delta.minWriterVersion' = '5'
);
