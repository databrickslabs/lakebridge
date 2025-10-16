import pandas as pd
import duckdb

from databricks.labs.lakebridge.connections.database_manager import FetchResult
from databricks.labs.lakebridge.resources.assessments.synapse.common.functions import set_logger

logger = set_logger(__name__)


def save_resultset_to_db(
    result: FetchResult,
    table_name: str,
    db_path: str,
    mode: str,
):
    """
    Saves a SQL result set to a DuckDB table using a predetermined schema.
    predetermined schemas align with queries being extracted in queries.py
    This method is not expected to evolve frequently, as the schemas are fixed.

    Args:
        result: A DBAPI cursor or result set object with `.keys()` and `.fetchall()` methods.
        table_name (str): The name of the DuckDB table to write to. Must exist in the predetermined schemas.
        db_path (str): Path to the DuckDB database file.
        mode (str): Write mode
        - 'overwrite' drops and recreates the table;
        - 'append' creates the table if it does not exist.

    Behavior:
        - Connects to the DuckDB database at db_path.
        - Checks if table exists in the database, creates it if not present
        - Uses a predetermined schema for the specified table_name.
        - Converts the result set to a pandas DataFrame.
        - Writes the DataFrame to the DuckDB table using the specified mode.

    """

    # Predetermined schemas

    schemas = {
        "dedicated_databases": "NAME STRING, DATABASE_ID BIGINT, CREATE_DATE STRING, STATE STRING, STATE_DESC STRING, COLLATION_NAME STRING",
        "serverless_databases": "NAME STRING, DATABASE_ID BIGINT, CREATE_DATE STRING, STATE STRING, STATE_DESC STRING, COLLATION_NAME STRING",
        "dedicated_tables": "TABLE_CATALOG STRING, TABLE_NAME STRING, TABLE_SCHEMA STRING, TABLE_TYPE STRING, POOL_NAME STRING",
        "serverless_tables": "TABLE_CATALOG STRING, TABLE_NAME STRING, TABLE_SCHEMA STRING, TABLE_TYPE STRING, POOL_NAME STRING",
        "dedicated_columns": "TABLE_CATALOG STRING, TABLE_SCHEMA STRING, TABLE_NAME STRING, COLUMN_NAME STRING, ORDINAL_POSITION BIGINT, COLUMN_DEFAULT STRING, IS_NULLABLE STRING, DATA_TYPE STRING, CHARACTER_MAXIMUM_LENGTH BIGINT, CHARACTER_OCTET_LENGTH BIGINT, NUMERIC_PRECISION BIGINT, NUMERIC_PRECISION_RADIX BIGINT, NUMERIC_SCALE BIGINT, DATETIME_PRECISION BIGINT, CHARACTER_SET_CATALOG STRING, CHARACTER_SET_SCHEMA STRING, CHARACTER_SET_NAME STRING, COLLATION_CATALOG STRING, COLLATION_SCHEMA STRING, COLLATION_NAME STRING, DOMAIN_CATALOG STRING, DOMAIN_SCHEMA STRING, DOMAIN_NAME STRING, POOL_NAME STRING",
        "serverless_columns": "TABLE_CATALOG STRING, TABLE_SCHEMA STRING, TABLE_NAME STRING, COLUMN_NAME STRING, ORDINAL_POSITION BIGINT, COLUMN_DEFAULT STRING, IS_NULLABLE STRING, DATA_TYPE STRING, CHARACTER_MAXIMUM_LENGTH BIGINT, CHARACTER_OCTET_LENGTH BIGINT, NUMERIC_PRECISION BIGINT, NUMERIC_PRECISION_RADIX BIGINT, NUMERIC_SCALE BIGINT, DATETIME_PRECISION BIGINT, CHARACTER_SET_CATALOG STRING, CHARACTER_SET_SCHEMA STRING, CHARACTER_SET_NAME STRING, COLLATION_CATALOG STRING, COLLATION_SCHEMA STRING, COLLATION_NAME STRING, DOMAIN_CATALOG STRING, DOMAIN_SCHEMA STRING, DOMAIN_NAME STRING, POOL_NAME STRING",
        "dedicated_views": "CHECK_OPTION STRING, IS_UPDATABLE STRING, TABLE_CATALOG STRING, TABLE_NAME STRING, TABLE_SCHEMA STRING, VIEW_DEFINITION STRING, POOL_NAME STRING",
        "serverless_views": "CHECK_OPTION STRING, IS_UPDATABLE STRING, TABLE_CATALOG STRING, TABLE_NAME STRING, TABLE_SCHEMA STRING, VIEW_DEFINITION STRING, POOL_NAME STRING",
        "dedicated_routines": "CREATED STRING, DATA_TYPE STRING, IS_DETERMINISTIC STRING, IS_IMPLICITLY_INVOCABLE STRING, IS_NULL_CALL STRING, IS_USER_DEFINED_CAST STRING, LAST_ALTERED STRING, MAX_DYNAMIC_RESULT_SETS BIGINT, NUMERIC_PRECISION BIGINT, NUMERIC_PRECISION_RADIX BIGINT, NUMERIC_SCALE BIGINT, ROUTINE_BODY STRING, ROUTINE_CATALOG STRING, ROUTINE_DEFINITION STRING, ROUTINE_NAME STRING, ROUTINE_SCHEMA STRING, ROUTINE_TYPE STRING, SCHEMA_LEVEL_ROUTINE STRING, SPECIFIC_CATALOG STRING, SPECIFIC_NAME STRING, SPECIFIC_SCHEMA STRING, SQL_DATA_ACCESS STRING, POOL_NAME STRING",
        "serverless_routines": "CREATED STRING, DATA_TYPE STRING, IS_DETERMINISTIC STRING, IS_IMPLICITLY_INVOCABLE STRING, IS_NULL_CALL STRING, IS_USER_DEFINED_CAST STRING, LAST_ALTERED STRING, MAX_DYNAMIC_RESULT_SETS BIGINT, NUMERIC_PRECISION BIGINT, NUMERIC_PRECISION_RADIX BIGINT, NUMERIC_SCALE BIGINT, ROUTINE_BODY STRING, ROUTINE_CATALOG STRING, ROUTINE_DEFINITION STRING, ROUTINE_NAME STRING, ROUTINE_SCHEMA STRING, ROUTINE_TYPE STRING, SCHEMA_LEVEL_ROUTINE STRING, SPECIFIC_CATALOG STRING, SPECIFIC_NAME STRING, SPECIFIC_SCHEMA STRING, SQL_DATA_ACCESS STRING, POOL_NAME STRING",
        "dedicated_sessions": "APP_NAME STRING, CLIENT_ID STRING, IS_TRANSACTIONAL BOOLEAN, LOGIN_NAME STRING, LOGIN_TIME STRING, QUERY_COUNT BIGINT, REQUEST_ID STRING, SESSION_ID STRING, SQL_SPID BIGINT, STATUS STRING, POOL_NAME STRING, EXTRACT_TS STRING",
        # Serverless sessions - matches SYS.DM_PDW_EXEC_SESSIONS for serverless
        "serverless_sessions": "ANSI_DEFAULTS BOOLEAN, ANSI_NULL_DFLT_ON BOOLEAN, ANSI_NULLS BOOLEAN, ANSI_PADDING BOOLEAN, ANSI_WARNINGS BOOLEAN, ARITHABORT BOOLEAN, AUTHENTICATING_DATABASE_ID BIGINT, CLIENT_INTERFACE_NAME STRING, CLIENT_VERSION BIGINT, CONCAT_NULL_YIELDS_NULL BOOLEAN, CONTEXT_INFO STRING, CPU_TIME BIGINT, DATABASE_ID BIGINT, DATE_FIRST BIGINT, DATE_FORMAT STRING, DEADLOCK_PRIORITY BIGINT, ENDPOINT_ID BIGINT, GROUP_ID BIGINT, HOST_NAME STRING, HOST_PROCESS_ID BIGINT, IS_FILTERED BOOLEAN, IS_USER_PROCESS BOOLEAN, LANGUAGE STRING, LAST_REQUEST_END_TIME STRING, LAST_REQUEST_START_TIME STRING, LOCK_TIMEOUT BIGINT, LOGICAL_READS BIGINT, LOGIN_NAME STRING, LOGIN_TIME STRING, MEMORY_USAGE BIGINT, NT_DOMAIN STRING, NT_USER_NAME STRING, OPEN_TRANSACTION_COUNT BIGINT, ORIGINAL_LOGIN_NAME STRING, ORIGINAL_SECURITY_ID STRING, PAGE_SERVER_READS BIGINT, PREV_ERROR BIGINT, PROGRAM_NAME STRING, QUOTED_IDENTIFIER BOOLEAN, READS BIGINT, ROW_COUNT BIGINT, SECURITY_ID STRING, SESSION_ID BIGINT, STATUS STRING, TEXT_SIZE BIGINT, TOTAL_ELAPSED_TIME BIGINT, TOTAL_SCHEDULED_TIME BIGINT, TRANSACTION_ISOLATION_LEVEL BIGINT, WRITES BIGINT, POOL_NAME STRING, EXTRACT_TS STRING",
        # Session requests metadata - matches list_requests() (SYS.DM_PDW_EXEC_REQUESTS + extract_ts)
        "dedicated_session_requests": "CLIENT_CORRELATION_ID STRING, COMMAND STRING, COMMAND2 STRING,COMMAND_TYPE STRING, DATABASE_ID BIGINT, END_COMPILE_TIME STRING, END_TIME STRING, ERROR_ID STRING, GROUP_NAME STRING, IMPORTANCE STRING, LABEL STRING, REQUEST_ID STRING, RESOURCE_ALLOCATION_PERCENTAGE DOUBLE, RESOURCE_CLASS STRING, RESULT_CACHE_HIT BIGINT, SESSION_ID STRING, START_TIME STRING, STATUS STRING, SUBMIT_TIME STRING, TOTAL_ELAPSED_TIME BIGINT, POOL_NAME STRING, EXTRACT_TS STRING",
        # Serverless session requests - matches SYS.DM_PDW_EXEC_REQUESTS for serverless
        "serverless_session_requests": "ANSI_DEFAULTS BOOLEAN, ANSI_NULL_DFLT_ON BOOLEAN, ANSI_NULLS BOOLEAN, ANSI_PADDING BOOLEAN, ANSI_WARNINGS BOOLEAN, ARITHABORT BOOLEAN, BLOCKING_SESSION_ID BIGINT, COMMAND STRING, CONCAT_NULL_YIELDS_NULL BOOLEAN, CONNECTION_ID STRING, CONTEXT_INFO STRING, CPU_TIME BIGINT, DATABASE_ID BIGINT, DATE_FIRST BIGINT, DATE_FORMAT STRING, DEADLOCK_PRIORITY BIGINT, DIST_STATEMENT_ID STRING, DOP BIGINT, ESTIMATED_COMPLETION_TIME BIGINT, EXECUTING_MANAGED_CODE BOOLEAN, GRANTED_QUERY_MEMORY BIGINT, GROUP_ID BIGINT, IS_RESUMABLE BOOLEAN, LANGUAGE STRING, LAST_WAIT_TYPE STRING, LOCK_TIMEOUT BIGINT, LOGICAL_READS BIGINT, NEST_LEVEL BIGINT, OPEN_RESULTSET_COUNT BIGINT, OPEN_TRANSACTION_COUNT BIGINT, PAGE_SERVER_READS BIGINT, PERCENT_COMPLETE DOUBLE, PLAN_HANDLE STRING, PREV_ERROR BIGINT, QUERY_HASH STRING, QUERY_PLAN_HASH STRING, QUOTED_IDENTIFIER BOOLEAN, READS BIGINT, REQUEST_ID BIGINT, ROW_COUNT BIGINT, SCHEDULER_ID BIGINT, SESSION_ID BIGINT, SQL_HANDLE STRING, START_TIME STRING, STATEMENT_END_OFFSET BIGINT, STATEMENT_START_OFFSET BIGINT, STATUS STRING, TASK_ADDRESS STRING, TEXT_SIZE BIGINT, TOTAL_ELAPSED_TIME BIGINT, TRANSACTION_ID BIGINT, TRANSACTION_ISOLATION_LEVEL BIGINT, USER_ID BIGINT, WAIT_RESOURCE STRING, WAIT_TIME BIGINT, WAIT_TYPE STRING, WRITES BIGINT, POOL_NAME STRING, EXTRACT_TS STRING",
        # Database storage info
        "dedicated_storage_info": "NODE_ID BIGINT, RESERVEDSPACEMB DOUBLE, USEDSPACEMB DOUBLE, POOL_NAME STRING, EXTRACT_TS STRING",
        # Table storage info - matches get_table_storage_info()
        "table_storage_info": "SCHEMA_NAME STRING, TABLE_NAME STRING, INDEX_NAME STRING, INDEX_TYPE STRING, ROW_COUNT BIGINT, COMPRESSION_TYPE STRING, TOTAL_SIZE_MB DOUBLE, USED_SIZE_MB DOUBLE, EXTRACT_TS STRING",
        # Query performance stats - matches get_query_performance_stats()
        "query_performance_stats": "EXECUTION_COUNT BIGINT, TOTAL_ELAPSED_TIME_SEC DOUBLE, TOTAL_WORKER_TIME_SEC DOUBLE, TOTAL_LOGICAL_READS BIGINT, TOTAL_PHYSICAL_READS BIGINT, TOTAL_LOGICAL_WRITES BIGINT, LAST_EXECUTION_TIME STRING, CREATION_TIME STRING, LAST_ELAPSED_TIME_SEC DOUBLE, LAST_WORKER_TIME_SEC DOUBLE, LAST_LOGICAL_READS BIGINT, LAST_PHYSICAL_READS BIGINT, LAST_LOGICAL_WRITES BIGINT, TOTAL_ROWS BIGINT, LAST_ROWS BIGINT, MIN_ROWS BIGINT, MAX_ROWS BIGINT, STATEMENT_START_OFFSET BIGINT, STATEMENT_END_OFFSET BIGINT, EXTRACT_TS STRING",
        "serverless_query_stats": "SQL_HANDLE STRING, PLAN_HANDLE STRING, STATEMENT_START_OFFSET BIGINT, STATEMENT_END_OFFSET BIGINT, CREATION_TIME STRING, LAST_EXECUTION_TIME STRING, EXECUTION_COUNT BIGINT, TOTAL_WORKER_TIME BIGINT, LAST_WORKER_TIME BIGINT, MIN_WORKER_TIME BIGINT, MAX_WORKER_TIME BIGINT, TOTAL_ELAPSED_TIME BIGINT, LAST_ELAPSED_TIME BIGINT, MIN_ELAPSED_TIME BIGINT, MAX_ELAPSED_TIME BIGINT, TOTAL_LOGICAL_READS BIGINT, LAST_LOGICAL_READS BIGINT, MIN_LOGICAL_READS BIGINT, MAX_LOGICAL_READS BIGINT, TOTAL_PHYSICAL_READS BIGINT, LAST_PHYSICAL_READS BIGINT, MIN_PHYSICAL_READS BIGINT, MAX_PHYSICAL_READS BIGINT, TOTAL_LOGICAL_WRITES BIGINT, LAST_LOGICAL_WRITES BIGINT, MIN_LOGICAL_WRITES BIGINT, MAX_LOGICAL_WRITES BIGINT, TOTAL_ROWS BIGINT, LAST_ROWS BIGINT, MIN_ROWS BIGINT, MAX_ROWS BIGINT, QUERY_HASH STRING, QUERY_PLAN_HASH STRING, STATEMENT_TEXT STRING",
        # Requests history - matches query_requests_history() (sys.dm_exec_requests_history)
        "serverless_requests_history": "STATUS STRING, TRANSACTION_ID BIGINT, DISTRIBUTED_STATEMENT_ID STRING, QUERY_HASH STRING, LOGIN_NAME STRING, START_TIME STRING, ERROR_CODE INTEGER, REJECTED_ROWS_PATH STRING, END_TIME STRING, COMMAND STRING, QUERY_TEXT STRING, TOTAL_ELAPSED_TIME_MS BIGINT, DATA_PROCESSED_MB BIGINT, ERROR STRING",
        # Data processed info
        "serverless_data_processed": "DATA_PROCESSED_MB BIGINT, TYPE STRING, POOL_NAME STRING, EXTRACT_TS STRING",
    }
    try:
        columns = list(result.columns)
        # Convert result to DataFrame
        df = pd.DataFrame(result.rows, columns=columns)
        logger.debug(df.columns)

        # Fetch the first batch
        if df.empty:
            logger.info(f"No data to save for table {table_name}.")
            return

        # Use predetermined schema if available, otherwise raise error
        if table_name in schemas:
            schema = schemas[table_name]
            logger.info(f"Using predetermined schema: {schema} for table: {table_name}")
        else:
            available_tables = list(schemas.keys())
            error_msg = f"Table '{table_name}' not found in predetermined schemas. Available tables: {available_tables}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        with duckdb.connect(db_path) as conn:
            logger.info(f"Connected to DuckDB database at {db_path}")
            tables = conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema='main' AND table_type='BASE TABLE';
                """
            ).fetchall()

            # Flatten the result
            list_tables = [row[0] for row in tables]
            # Handle write modes
            if mode == "overwrite":
                conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                conn.execute(f"CREATE TABLE {table_name} ({schema})")
            elif mode == "append" and table_name not in list_tables:
                conn.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({schema})")

            logger.info(f"Tables created: {table_name}")
            conn.register("df_view", df)
            # Insert data from the DataFrame view
            conn.execute(f"INSERT INTO {table_name} SELECT * FROM df_view")

        logger.info(f"Successfully saved resultset to DuckDB table {table_name} in {db_path}")
    except Exception as e:
        logger.error(f"Error in save_resultset_to_db for table {table_name}: {str(e)}")


def get_max_column_value_duckdb(
    column_name: str,
    table_name: str,
    db_path: str,
):
    """
    Get the maximum value of a column from a DuckDB table.
    """
    max_column_val = None
    try:
        with duckdb.connect(db_path) as conn:
            # Check if table exists
            table_exists = table_name in conn.execute("SHOW TABLES").fetchdf()['name'].values
            if not table_exists:
                logger.info(f"Table {table_name} does not exist in DuckDB. Returning None.")
                return None
            max_column_query = f"SELECT MAX({column_name}) AS last_{column_name} FROM {table_name}"
            logger.info(f"get_max_column_value_duckdb:: query {max_column_query}")
            rows = conn.execute(max_column_query).fetchall()
            max_column_val = rows[0][0] if rows else None
    except Exception as e:
        logger.error(f"ERROR: {e}")
    logger.info(f"max_column_val = {max_column_val}")
    return max_column_val


def insert_df_to_duckdb(df: pd.DataFrame, db_path: str, table_name: str) -> None:
    """
    Insert a pandas DataFrame into a DuckDB table.
    """
    try:
        with duckdb.connect(db_path) as conn:
            # Drop existing table if it exists
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")

            if df.empty:
                # If DataFrame is empty, create an empty table with the correct schema
                if len(df.columns) > 0:
                    conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df LIMIT 0")
                    logger.info(f"Created empty table {table_name} with schema: {df.columns.tolist()}")
                else:
                    logger.warning(f"Skipping table {table_name} creation as DataFrame has no columns")
                return
            # Create the table with the DataFrame's schema and insert data
            conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df")
            logger.info(f"Successfully inserted {len(df)} rows into {table_name} table")
    except Exception as e:
        logger.error(f"Error inserting data into DuckDB: {str(e)}")
        raise
