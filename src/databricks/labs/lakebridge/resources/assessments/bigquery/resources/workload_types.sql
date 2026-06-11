-- This procedure splits the historical workloads on BQ
-- between ETL and BI

-- Excluded the SCRIPT statement type, otherwise some values might be counted twice.
-- This is because the SCRIPT row includes summary values for all child jobs that were executed as part of this job.

WITH STATEMENT_CLASSIFICATION AS
(
SELECT * FROM UNNEST([STRUCT<statement_type string, workload_category STRING>
("SELECT", "BI"),
("ASSERT", "ETL"),
("INSERT", "ETL"),
("UPDATE", "ETL"),
("DELETE", "ETL"),
("MERGE", "ETL"),
("CREATE_TABLE", "ETL"),
("CREATE_TABLE_AS_SELECT", "ETL"),
("CREATE_VIEW","ETL"),
("CREATE_MODEL", "ML"),
("CREATE_MATERIALIZED_VIEW", "ETL"),
("CREATE_FUNCTION", "ETL"),
("CREATE_PROCEDURE", "ETL"),
("CREATE_SCHEMA", "ETL"),
("DROP_TABLE", "ETL"),
("DROP_EXTERNAL_TABLE", "ETL"),
("DROP_VIEW", "ETL"),
("DROP_MATERIALIZED_VIEW", "ETL"),
("DROP_FUNCTION", "ETL"),
("DROP_PROCEDURE", "ETL"),
("DROP_SCHEMA", "ETL"),
("ALTER_TABLE", "ETL"),
("ALTER_VIEW", "ETL"),
("ALTER_MATERIALIZED_VIEW", "ETL"),
("SCRIPT", "ETL"),
("TRUNCATE_TABLE", "ETL"),
("CREATE_EXTERNAL_TABLE", "ETL"),
("EXPORT_DATA", "ETL"),
("CALL", "ETL"),
("QUERY_STATEMENT_TYPE_UNSPECIFIED", "Other"),
(null, "BI")
])
)
SELECT sum(num_jobs) as total_jobs,
        CAST(sum(slot_ms) as NUMERIC) / (3600.0 * 1000.0) AS total_slot_hours,
        CAST(sum(bytes_processed) AS NUMERIC) / POWER(2.0, 40) AS total_tb_processed,
        workload_type,
        '{{project_region}}' AS metadata_level
FROM(
        SELECT count(j.job_id) as num_jobs,
                sum(j.total_slot_ms) as slot_ms,
                sum(j.total_bytes_processed) as bytes_processed,
                j.job_type,
                j.statement_type,
        CASE
        WHEN stmt.workload_category is NULL AND job_type = 'QUERY' THEN 'BI'
        WHEN stmt.workload_category is NULL AND job_type = 'COPY' THEN 'ETL'
        WHEN stmt.workload_category is NULL AND job_type = 'LOAD' THEN 'ETL'
        ELSE stmt.workload_category
        END as workload_type
        FROM `{{project_region}}`.INFORMATION_SCHEMA.JOBS j
        LEFT JOIN STATEMENT_CLASSIFICATION stmt
        ON j.statement_type = stmt.statement_type
        WHERE j.start_time > timestamp_sub(current_timestamp, INTERVAL {{profiling_window_in_days}} DAY)
        AND (j.statement_type != 'SCRIPT' OR j.statement_type IS NULL)
        GROUP by job_type, j.statement_type, workload_type
)
GROUP BY workload_type;
