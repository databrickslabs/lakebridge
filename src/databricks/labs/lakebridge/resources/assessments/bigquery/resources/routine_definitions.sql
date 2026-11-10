-- User-defined routines (functions, table functions, aggregate functions, procedures)
-- from INFORMATION_SCHEMA.ROUTINES, including recreation DDL.
-- Duplicate specific_* identifiers and always-null security_type are omitted.
SELECT
  routine_catalog,
  routine_schema,
  routine_name,
  routine_type,
  data_type,
  routine_body,
  routine_definition,
  external_language,
  is_deterministic,
  created,
  last_altered,
  ddl,
  connection
FROM `{{project_region}}`.INFORMATION_SCHEMA.ROUTINES
;
