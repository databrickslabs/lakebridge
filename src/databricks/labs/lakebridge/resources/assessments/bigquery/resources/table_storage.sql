-- data volume relevant for migration is:
-- total_physical_tb_to_transfer = active_physical_tb + long_term_physical_tb + time_travel_physical_tb + fail_safe_physical_tb

-- table types under consideration:
-- 'BASE TABLE': Standard BigQuery tables storing data internally. Considered.
-- 'VIEW': Virtual tables defined by a SQL query (standard views). Ignored.
-- 'EXTERNAL': Tables referencing data stored outside of BigQuery (external tables). Ignored.
-- 'MATERIALIZED VIEW': Precomputed views that store query results for performance optimization. Considered.
-- 'SNAPSHOT': Read-only, point-in-time copies of a base table. Ignored.
-- 'CLONE': Duplicates of a table at a specific time without copying the data. Ignored.
-- 'MODEL': A machine learning model rather than a standard table, view, or any other table type. Ignored.

-- billing model
-- ----------------------------------------------------------------------------
-- Each dataset is configured with `storage_billing_model = LOGICAL` (default)
-- or `PHYSICAL`. Both `active_logical_bytes` and `active_physical_bytes` are
-- populated in INFORMATION_SCHEMA.TABLE_STORAGE regardless — only the bytes
-- matching the dataset's billing model are actually billed. We extract the
-- billing model from each dataset's DDL via REGEXP and roll up per (project,
-- region, billing_model). Datasets that don't specify the option fall through
-- to LOGICAL (BQ default). The post-analysis job applies the per-region rate
-- to the right bytes for each model.

WITH dataset_billing_model AS (
  SELECT
    schema_name,
    COALESCE(
      REGEXP_EXTRACT(ddl, r'''storage_billing_model\s*=\s*['"]?([^'",\s)]+)'''),
      'LOGICAL'
    ) AS storage_billing_model
  FROM `{{project_region}}`.INFORMATION_SCHEMA.SCHEMATA
),
table_storage_with_model AS (
  SELECT ts.*, COALESCE(dbm.storage_billing_model, 'LOGICAL') AS storage_billing_model
  FROM `{{project_region}}`.INFORMATION_SCHEMA.TABLE_STORAGE ts
  LEFT JOIN dataset_billing_model dbm ON ts.table_schema = dbm.schema_name
  WHERE ts.total_physical_bytes > 0
    AND ts.table_type IN ('BASE TABLE', 'MATERIALIZED VIEW')
)
SELECT
    '{{project_region}}' as metadata_level,
    SUM(IF(deleted=false, active_logical_bytes, 0)) / power(1024, 4) AS active_logical_tb,
    SUM(IF(deleted=false, long_term_logical_bytes, 0)) / power(1024, 4) AS long_term_logical_tb,
    SUM(active_physical_bytes) / power(1024, 4) AS active_physical_tb,
    SUM(active_physical_bytes - time_travel_physical_bytes) / power(1024, 4) AS active_no_tt_physical_tb,
    SUM(long_term_physical_bytes) / power(1024, 4) AS long_term_physical_tb,
    SUM(time_travel_physical_bytes) / power(1024, 4) AS time_travel_physical_tb,
    SUM(fail_safe_physical_bytes) / power(1024, 4) AS fail_safe_physical_tb,
    SUM(active_physical_bytes + long_term_physical_bytes
        + time_travel_physical_bytes + fail_safe_physical_bytes) / power(1024, 4)
      AS total_physical_tb_to_migrate,
    storage_billing_model,
    COUNT(DISTINCT table_schema) AS dataset_count
FROM table_storage_with_model
GROUP BY metadata_level, storage_billing_model
ORDER BY storage_billing_model;
