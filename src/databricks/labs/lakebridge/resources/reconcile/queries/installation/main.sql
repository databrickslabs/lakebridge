CREATE TABLE IF NOT EXISTS main (
    recon_table_id BIGINT NOT NULL,
    recon_id STRING NOT NULL,
    source_type STRING NOT NULL,
    source_table STRUCT<
                         catalog: STRING,
                         schema: STRING NOT NULL,
                         table_name: STRING NOT NULL
                        > ,
    target_table STRUCT<
                         catalog: STRING NOT NULL,
                         schema: STRING NOT NULL,
                         table_name: STRING NOT NULL
                        > NOT NULL,
    report_type STRING NOT NULL,
    operation_name  STRING NOT NULL,
    start_ts TIMESTAMP,
    end_ts TIMESTAMP
)
TBLPROPERTIES (
    'delta.columnMapping.mode' = 'name',
    'delta.minReaderVersion' = '2',
    'delta.minWriterVersion' = '5'
);
