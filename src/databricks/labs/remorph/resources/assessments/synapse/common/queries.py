class SynapseQueries:
    @staticmethod
    def list_databases() -> str:
        """Get list of databases"""
        return """
               SELECT
                   NAME,
                   DATABASE_ID,
                   CREATE_DATE,
                   STATE,
                   STATE_DESC,
                   COLLATION_NAME
               FROM SYS.DATABASES WHERE NAME <> 'master' ;
               """

    @staticmethod
    def list_tables() -> str:
        """Get list of tables"""
        return """
               SELECT
                   TABLE_CATALOG,
                   TABLE_SCHEMA,
                   TABLE_NAME,
                   TABLE_TYPE
               FROM INFORMATION_SCHEMA.TABLES ;
               """

    @staticmethod
    def list_columns() -> str:
        """Get list of columns"""
        return """
               SELECT
                   TABLE_CATALOG,
                   TABLE_SCHEMA,
                   TABLE_NAME,
                   COLUMN_NAME,
                   ORDINAL_POSITION,
                   COLUMN_DEFAULT,
                   IS_NULLABLE,
                   DATA_TYPE,
                   CHARACTER_MAXIMUM_LENGTH,
                   CHARACTER_OCTET_LENGTH,
                   NUMERIC_PRECISION,
                   NUMERIC_PRECISION_RADIX,
                   NUMERIC_SCALE,
                   DATETIME_PRECISION,
                   CHARACTER_SET_CATALOG,
                   CHARACTER_SET_SCHEMA,
                   CHARACTER_SET_NAME,
                   COLLATION_CATALOG,
                   COLLATION_SCHEMA,
                   COLLATION_NAME,
                   DOMAIN_CATALOG,
                   DOMAIN_SCHEMA,
                   DOMAIN_NAME
               FROM INFORMATION_SCHEMA.COLUMNS ;
               """

    @staticmethod
    def list_views(redact_sql_text: bool = False) -> str:
        """Get list of views"""
        return """
               SELECT
                   TABLE_CATALOG,
                   TABLE_SCHEMA,
                   TABLE_NAME,
                   CHECK_OPTION,
                   IS_UPDATABLE,
                   '[REDACTED]' as VIEW_DEFINITION
               FROM INFORMATION_SCHEMA.VIEWS
               """

    @staticmethod
    def list_routines(redact_sql_text: bool = False) -> str:
        """Get list of routines (functions + procedures)"""
        return """
                   SELECT
                       ROUTINE_CATALOG,
                       ROUTINE_SCHEMA,
                       ROUTINE_NAME,
                       ROUTINE_TYPE,
                       DATA_TYPE,
                       CHARACTER_MAXIMUM_LENGTH,
                       NUMERIC_PRECISION,
                       NUMERIC_SCALE,
                       DATETIME_PRECISION,
                       ROUTINE_BODY,
                       IS_DETERMINISTIC,
                       SQL_DATA_ACCESS,
                       IS_NULL_CALL,
                       '[REDACTED]' as ROUTINE_DEFINITION
                   FROM information_schema.routines
                   """

    @staticmethod
    def list_sessions(last_login_time: str | None = None) -> str:
        """Get session list with transformed login names and client IDs"""
        return """
                     SELECT
                      *,
                      CURRENT_TIMESTAMP as extract_ts
                     FROM SYS.DM_PDW_EXEC_SESSIONS
                     WHERE start_time IS NOT NULL
                       AND command IS NOT NULL
                     """

    @staticmethod
    def list_requests(min_end_time: str | None = None) -> str:
        """Get session request list with command type classification"""
        base_query = """
                              SELECT *
                                  CURRENT_TIMESTAMP as extract_ts
                            FROM SYS.DM_PDW_EXEC_REQUESTS
                            WHERE start_time IS NOT NULL
                            AND command IS NOT NULL
                            {end_time_filter}
                     """

        end_time_filter = f"AND end_time > '{min_end_time}'" if min_end_time else ""
        command_redaction = "'[REDACTED]' as command"

        return (
            base_query.format(end_time_filter=end_time_filter, command_redaction=command_redaction).strip().rstrip(';')
        )

    @staticmethod
    def get_db_storage_info() -> str:
        """Get database storage information"""
        return """
               SELECT *, CURRENT_TIMESTAMP AS EXTRACT_TS
               FROM (
                        SELECT
                            PDW_NODE_ID AS NODE_ID,
                            (SUM(RESERVED_PAGE_COUNT) * 8) / 1024 AS RESERVEDSPACEMB,
                            (SUM(USED_PAGE_COUNT)  * 8) / 1024 AS USEDSPACEMB
                        FROM SYS.DM_PDW_NODES_DB_PARTITION_STATS
                        GROUP BY PDW_NODE_ID
                    ) X
               """

    @staticmethod
    def get_table_storage_info() -> str:
        """Get detailed table storage information"""
        return """
               SELECT
                   OBJECT_SCHEMA_NAME(P.OBJECT_ID) AS SCHEMA_NAME,
                   OBJECT_NAME(P.OBJECT_ID) AS TABLE_NAME,
                   I.NAME AS INDEX_NAME,
                   I.TYPE_DESC AS INDEX_TYPE,
                   P.ROWS AS ROW_COUNT,
                   P.DATA_COMPRESSION_DESC AS COMPRESSION_TYPE,
                   AU.TOTAL_PAGES * 8.0 / 1024 AS TOTAL_SIZE_MB,
                   AU.USED_PAGES * 8.0 / 1024 AS USED_SIZE_MB,
                   CURRENT_TIMESTAMP AS EXTRACT_TS
               FROM SYS.PARTITIONS P
                        INNER JOIN SYS.INDEXES I ON P.OBJECT_ID = I.OBJECT_ID AND P.INDEX_ID = I.INDEX_ID
                        INNER JOIN SYS.ALLOCATION_UNITS AU ON P.PARTITION_ID = AU.CONTAINER_ID
               WHERE P.OBJECT_ID > 255
               ORDER BY AU.TOTAL_PAGES DESC
               """

    @staticmethod
    def get_query_performance_stats(days: int = 7) -> str:
        """Get query performance statistics"""
        return f"""
            SELECT
                qs.EXECUTION_COUNT,
                QS.TOTAL_ELAPSED_TIME / 1000000.0 AS TOTAL_ELAPSED_TIME_SEC,
                QS.TOTAL_WORKER_TIME / 1000000.0 AS TOTAL_WORKER_TIME_SEC,
                QS.TOTAL_LOGICAL_READS,
                QS.TOTAL_PHYSICAL_READS,
                QS.TOTAL_LOGICAL_WRITES,
                QS.LAST_EXECUTION_TIME,
                QS.CREATION_TIME,
                QS.LAST_ELAPSED_TIME / 1000000.0 AS LAST_ELAPSED_TIME_SEC,
                QS.LAST_WORKER_TIME / 1000000.0 AS LAST_WORKER_TIME_SEC,
                QS.LAST_LOGICAL_READS,
                QS.LAST_PHYSICAL_READS,
                QS.LAST_LOGICAL_WRITES,
                QS.TOTAL_ROWS,
                QS.LAST_ROWS,
                QS.MIN_ROWS,
                QS.MAX_ROWS,
                QS.STATEMENT_START_OFFSET,
                QS.STATEMENT_END_OFFSET,
                CURRENT_TIMESTAMP as EXTRACT_TS
            FROM SYS.DM_EXEC_QUERY_STATS QS
            WHERE QS.last_execution_time >= DATEADD(day, -{days}, GETDATE())
            ORDER BY QS.total_elapsed_time DESC
        """

    @staticmethod
    def list_query_stats(min_last_execution_time) -> str:
        """
        get the query stats
        source for below query:
        https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-query-stats-transact-sql?view=sql-server-ver16#b-returning-row-count-aggregates-for-a-query

        """
        return f"""
      SELECT QS.*,
        SUBSTRING(
          ST.text,
          (QS.statement_start_offset / 2) + 1,
          (
            (
              CASE
                statement_end_offset
                WHEN -1 THEN DATALENGTH(ST.text)
                ELSE QS.statement_end_offset
              END - QS.statement_start_offset
            ) / 2
          ) + 1
        ) AS statement_text
      FROM sys.dm_exec_query_stats AS QS
        CROSS APPLY sys.dm_exec_sql_text(QS.sql_handle) as ST
      {"WHERE QS.last_execution_time > '"+min_last_execution_time+"'" if min_last_execution_time else ""}"""

    @staticmethod
    def query_requests_history(min_end_time) -> str:
        return f"""SELECT * FROM sys.dm_exec_requests_history {"WHERE end_time > '"+min_end_time+"'" if min_end_time else ""}"""
