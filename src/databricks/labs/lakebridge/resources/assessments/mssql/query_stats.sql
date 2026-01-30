/**
 * Retrieves and classifies recently executed SQL statements from `sys.dm_exec_query_stats`.
 * Includes execution metrics (count, duration, CPU time, rows) and categorizes each statement
 * as QUERY, DML, DDL, ROUTINE, TRANSACTION_CONTROL, or OTHER based on its command type.
 */
with query_stats as (
  SELECT
      CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', qs.sql_handle), 1) as sql_handle,
      st.dbid,
      qs.creation_time,
      qs.last_execution_time,
      qs.execution_count,
      qs.total_worker_time,
      qs.total_elapsed_time,
      qs.total_rows,
      SUBSTRING(st.text, (qs.statement_start_offset/2) + 1,
          ((CASE statement_end_offset
              WHEN -1 THEN DATALENGTH(st.text)
              ELSE qs.statement_end_offset END
              - qs.statement_start_offset)/2) + 1) AS statement_text
  FROM sys.dm_exec_query_stats AS qs
  CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) AS st
),
  query_stats_ex as (
  select
    dbid,
    creation_time,
    last_execution_time,
    execution_count,
    total_worker_time,
    total_elapsed_time,
    total_rows,
    UPPER(SUBSTRING(LTRIM(RTRIM(statement_text)), 1, 40)) as command
  from query_stats
  )
  SELECT
  *,
  CASE
    WHEN command like 'SELECT%' THEN 'QUERY'
    WHEN command like 'WITH%' THEN 'QUERY'
    WHEN command like 'INSERT%' THEN 'DML'
    WHEN command like 'UPDATE%' THEN 'DML'
    WHEN command like 'MERGE%' THEN 'DML'
    WHEN command like 'DELETE%' THEN 'DML'
    WHEN command like 'TRUNCATE%' THEN 'DML'
    WHEN command like 'COPY%' THEN 'DML'
    WHEN command like 'IF%' THEN 'DML'
    WHEN command like 'BEGIN%' THEN 'DML'
    WHEN command like 'DECLARE%' THEN 'DML'
    WHEN command like 'BUILDREPLICATEDTABLECACHE%' THEN 'DML'
    WHEN command like 'CREATE%' THEN 'DDL'
    WHEN command like 'DROP%' THEN 'DDL'
    WHEN command like 'ALTER%' THEN 'DDL'
    WHEN command like 'EXEC%' THEN 'ROUTINE'
    WHEN command like 'EXECUTE %' THEN 'ROUTINE'
    WHEN command like 'BEGIN%TRAN%' THEN 'TRANSACTION_CONTROL'
    WHEN command like 'END%TRAN%' THEN 'TRANSACTION_CONTROL'
    WHEN command like 'COMMIT%' THEN 'TRANSACTION_CONTROL'
    WHEN command like 'ROLLBACK%' THEN 'TRANSACTION_CONTROL'
    ELSE 'OTHER'
  END as command_type,
  SYSDATETIME() as extract_ts
  FROM query_stats_ex
  ORDER BY last_execution_time
