class MSSQLQueries:

    @staticmethod
    def get_query_stats(last_execution_time: str | None) -> str:
        """
        Retrieves and classifies recently executed SQL statements from `sys.dm_exec_query_stats`.
        Includes execution metrics (count, duration, CPU time, rows) and categorizes each statement as QUERY, DML, DDL,
        ROUTINE, TRANSACTION_CONTROL, or OTHER based on its command type.
        """
        predicate = (
            f"WHERE qs.last_execution_time > CAST('{last_execution_time}' AS DATETIME2(6))"
            if last_execution_time
            else ""
        )

        return f"""
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
            {predicate}
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
        """

    @staticmethod
    def get_procedure_stats(last_execution_time: str | None):
        """
        Retrieves execution statistics for stored procedures from `sys.dm_exec_procedure_stats`,
        including execution counts, total CPU and elapsed time, last execution timestamp,
        and maps object and database IDs to their names. Results are ordered by most recent execution.
        """
        predicate = (
            f"WHERE last_execution_time > CAST('{last_execution_time}' AS DATETIME2(6))" if last_execution_time else ""
        )
        return f"""
            SELECT
              database_id,
              DB_NAME(database_id) AS db_name,
              object_id,
              OBJECT_NAME(object_id, database_id) AS object_name,
              type,
              last_execution_time,
              execution_count,
              total_worker_time,
              total_elapsed_time,
              SYSDATETIME() as extract_ts
            FROM
              sys.dm_exec_procedure_stats
            {predicate}
            ORDER BY
              last_execution_time
        """

    @staticmethod
    def get_sessions(last_execution_time: str | None):
        """
        Retrieves active user session details from `sys.dm_exec_sessions`, including login info,
        program and client names, CPU and memory usage, request timing, row counts, and database context.
        Excludes system sessions and orders results by the end time of the last request.
        """
        predicate = (
            f"AND last_request_end_time > CAST('{last_execution_time}' AS DATETIME2(6))" if last_execution_time else ""
        )
        return f"""
        SELECT
          session_id,
          login_time,
          program_name,
          client_interface_name,
          CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', login_name), 1) as login_name,
          status,
          cpu_time,
          memory_usage,
          total_scheduled_time,
          total_elapsed_time,
          last_request_start_time,
          last_request_end_time,
          is_user_process,
          row_count,
          database_id,
          DB_NAME(database_id) AS db_name,
          SYSDATETIME() as extract_ts
        FROM
          sys.dm_exec_sessions
        WHERE
          is_user_process <> 0 {predicate}
        ORDER BY
          last_request_end_time
        """

    @staticmethod
    def get_cpu_utilization(last_execution_time: str | None):
        """
        Extracts SQL Server CPU and system utilization metrics from `sys.dm_os_ring_buffers`,
        including system idle and SQL process utilization over time.
        """
        predicate = f"WHERE EventTime > CAST('{last_execution_time}' AS DATETIME2(6))" if last_execution_time else ""
        return f"""
            WITH process_utilization_info
                 AS (SELECT record.value('(./Record/@id)[1]', 'int')
                               AS record_id,
                            [timestamp],
            record.value('(./Record/SchedulerMonitorEvent/SystemHealth/SystemIdle)[1]', 'int')         AS SystemIdle,
            record.value('(./Record/SchedulerMonitorEvent/SystemHealth/ProcessUtilization)[1]', 'int') AS SQLProcessUtilization
             FROM   (SELECT [timestamp],
                            CONVERT(XML, record) AS record
                     FROM   sys.dm_os_ring_buffers
                     WHERE  ring_buffer_type = N'RING_BUFFER_SCHEDULER_MONITOR'
                            AND record LIKE '%<SystemHealth>%') X),
                 os_sysinfo
                 AS (SELECT TOP 1 ms_ticks
                     FROM   sys.dm_os_sys_info),
                 cpu_utilization
                 AS (SELECT record_id,
                            Dateadd (ms, ( [timestamp] - ms_ticks ), Getdate()) AS EventTime,
                            systemidle,
                            sqlprocessutilization
                     FROM   process_utilization_info
                            CROSS JOIN os_sysinfo)
            SELECT *,
                   Sysdatetime() AS extract_ts
            FROM   cpu_utilization
            {predicate}
            ORDER  BY eventtime
        """

    @staticmethod
    def get_sys_info():
        """
        Retrieves system-level information from SQL Server using sys.dm_os_sys_info.
        Returns details about memory, CPU, scheduler count, and other OS-related
        metadata for the SQL Server instance, along with a timestamp indicating when
        the data was extracted.
        """
        return """
        SELECT *,
               Sysdatetime() AS extract_ts
        FROM   sys.dm_os_sys_info
        """

    @staticmethod
    def get_databases():
        """
        Retrieve metadata for all user databases, excluding system databases.
        Returns each database's ID, name, collation, creation date,
        and a timestamp indicating when the data was extracted.
        """
        return """
            SELECT DB_ID(NAME) AS db_id,
                   NAME,
                   collation_name,
                   create_date,
                   SYSDATETIME() AS extract_ts
            FROM   sys.databases
            WHERE  NAME NOT IN ( 'master', 'tempdb', 'model', 'msdb' );
          """

    @staticmethod
    def get_tables(db_name: str):
        """
        Retrieves metadata for all tables in the specified database by querying
        INFORMATION_SCHEMA.TABLES. Returns table definitions along with a timestamp
        indicating when the data was extracted.
        """
        return f"""
        SELECT
            *,
            SYSDATETIME() as extract_ts
          FROM {db_name}.information_schema.tables
        """

    @staticmethod
    def get_views(db_name: str):
        """
        Retrieves metadata for all views in the specified database by querying
        `INFORMATION_SCHEMA.VIEWS`. Returns view definitions along with a timestamp
        indicating when the data was extracted.
        """
        return f"""
        SELECT
           *,
           SYSDATETIME() as extract_ts
          FROM {db_name}.information_schema.views
        """

    @staticmethod
    def get_columns(db_name: str):
        """
        Retrieves column-level metadata for all tables and views in the specified
        database by querying INFORMATION_SCHEMA.COLUMNS. Returns column attributes
        along with a timestamp indicating when the data was extracted.
        """
        return f"""
        SELECT
           *,
           SYSDATETIME() as extract_ts
        FROM {db_name}.information_schema.columns
        """

    @staticmethod
    def get_indexed_views(db_name: str):
        """
        Retrieves metadata for all indexed views in the specified database by joining
        `sys.views` with `sys.indexes`. Returns view details for those with a clustered
        index (index_id = 1) along with a timestamp indicating when the data was extracted.
        """
        return f"""
        SELECT A.*, SYSDATETIME() as extract_ts
            FROM {db_name}.sys.views A
            JOIN {db_name}.sys.indexes B ON A.object_id = B.object_id
            WHERE B.index_id = 1
        """

    @staticmethod
    def get_routines(db_name: str):
        """
        Retrieves metadata for all routines (stored procedures and functions) in the
        specified database by querying INFORMATION_SCHEMA.ROUTINES. Returns routine
        details along with a timestamp indicating when the data was extracted.
        """
        return f"""
        select
           *,
           SYSDATETIME() as extract_ts
        from {db_name}.information_schema.routines"""

    @staticmethod
    def get_db_sizes(db_name: str):
        """
        Retrieves metadata for all data files (type = 0) in the specified database
        from sys.database_files. Returns file name, type, current size, free space,
        maximum size, and a timestamp indicating when the data was extracted.
        """
        return f"""
        SELECT
           '{db_name}' AS DbName,
           name AS FileName,
           type_desc,
           size/128.0 AS CurrentSizeMB,
           size/128.0 - CAST(FILEPROPERTY(name, 'SpaceUsed') AS int)/128.0 AS FreeSpaceInMB,
           max_size as MaxSize,
           SYSDATETIME() as extract_ts
          FROM {db_name}.sys.database_files WHERE type=0
        """

    @staticmethod
    def get_table_sizes(db_name: str):
        """
        Retrieves storage and row count statistics for all user tables in the specified
        database by querying sys.dm_db_partition_stats and sys.objects. Returns table
        name, total rows, reserved, used, and unused space (MB), breakdown of data vs.
        index space, and a timestamp indicating when the data was extracted.
        """
        return f"""
        SELECT
            o.name AS TableName,
            SUM(ps.row_count) AS [RowCount],
            SUM(ps.reserved_page_count) * 8 / 1024 AS ReservedMB,
            SUM(ps.used_page_count) * 8 / 1024 AS UsedMB,
            (SUM(ps.reserved_page_count) - SUM(ps.used_page_count)) * 8 / 1024 AS UnusedMB,
            SUM(CASE
                    WHEN ps.index_id < 2 THEN ps.in_row_data_page_count + ps.lob_used_page_count + ps.row_overflow_used_page_count
                    ELSE 0
                END) * 8 / 1024 AS DataMB,
            SUM(CASE
                    WHEN ps.index_id >= 2 THEN ps.in_row_data_page_count
                    ELSE 0
                END) * 8 / 1024 AS IndexMB,
            SYSDATETIME() as extract_ts
          FROM  {db_name}.sys.dm_db_partition_stats AS ps
          JOIN  {db_name}sys.objects AS o ON ps.object_id = o.object_id
          WHERE o.type = 'U'
          GROUP BY schema_name(o.schema_id), o.name
        """
