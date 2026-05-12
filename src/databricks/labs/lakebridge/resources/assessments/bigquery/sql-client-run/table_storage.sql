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

DECLARE metadatalevel STRING DEFAULT 'region-us';
    
-- SET metadatalevel to the format <project>.<region>
SET metadatalevel = 'my-gcp-project.region-us';

EXECUTE IMMEDIATE 
FORMAT("""
WITH TABLE_STORAGE_ANALYSIS AS
(
SELECT
    @metadatalevel as metadatalevel,
     -- Logical
     SUM(IF(deleted=false, active_logical_bytes, 0)) / power(1024, 4) AS active_logical_tb,
     SUM(IF(deleted=false, long_term_logical_bytes, 0)) / power(1024, 4) AS long_term_logical_tb,
     -- Physical
     SUM(active_physical_bytes) / power(1024, 4) AS active_physical_tb,
     SUM(active_physical_bytes - time_travel_physical_bytes) / power(1024, 4) AS active_no_tt_physical_tb,
     SUM(long_term_physical_bytes) / power(1024, 4) AS long_term_physical_tb,
     -- Restorable previously deleted physical
     SUM(time_travel_physical_bytes) / power(1024, 4) AS time_travel_physical_tb,
     SUM(fail_safe_physical_bytes) / power(1024, 4) AS fail_safe_physical_tb
   FROM
     `%s`.INFORMATION_SCHEMA.TABLE_STORAGE
   WHERE total_physical_bytes > 0
   AND table_type IN ('BASE TABLE', 'MATERIALIZED VIEW')
)
SELECT *, 
(active_physical_tb + long_term_physical_tb + time_travel_physical_tb + fail_safe_physical_tb) as total_physical_tb_to_migrate
FROM TABLE_STORAGE_ANALYSIS;""", 
metadatalevel)
USING 
metadatalevel AS metadatalevel