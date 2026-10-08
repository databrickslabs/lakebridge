-- Resolved top-level column metadata from INFORMATION_SCHEMA.COLUMNS.
-- Nested STRUCT/ARRAY type expressions are preserved in data_type; COLUMN_FIELD_PATHS
-- is intentionally not extracted (too granular for the current consumer).
-- policy_tags is an ARRAY; serialize to JSON string for DuckDB portability.
SELECT
  table_catalog,
  table_schema,
  table_name,
  column_name,
  ordinal_position,
  is_nullable,
  data_type,
  is_hidden,
  is_system_defined,
  is_partitioning_column,
  clustering_ordinal_position,
  collation_name,
  column_default,
  rounding_mode,
  TO_JSON_STRING(policy_tags) AS policy_tags
FROM `{{project_region}}`.INFORMATION_SCHEMA.COLUMNS
;
