-- Object inventory + recreation DDL from INFORMATION_SCHEMA.TABLES.
-- base_table_* / snapshot_time_ms capture clone/snapshot structural provenance.
-- replica_source_* identifies the source of a materialized-view replica.
-- Transient replication health fields (replication_status / replication_error) are omitted.
SELECT
  table_catalog,
  table_schema,
  table_name,
  table_type,
  managed_table_type,
  creation_time,
  base_table_catalog,
  base_table_schema,
  base_table_name,
  snapshot_time_ms,
  replica_source_catalog,
  replica_source_schema,
  replica_source_name,
  ddl,
  default_collation_name
FROM `{{project_region}}`.INFORMATION_SCHEMA.TABLES
;
