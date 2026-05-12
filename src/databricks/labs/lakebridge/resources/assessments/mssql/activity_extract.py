import json
import sys

from databricks.labs.blueprint.entrypoint import get_logger

from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.resources.assessments.mssql.common.connector import get_sqlserver_reader
from databricks.labs.lakebridge.resources.assessments.mssql.common.queries import MSSQLQueries
from databricks.labs.lakebridge.resources.assessments.synapse.common.duckdb_helpers import save_resultset_to_db
from databricks.labs.lakebridge.resources.assessments.synapse.common.functions import arguments_loader

logger = get_logger(__file__)


def execute():

    db_path, creds_file = arguments_loader(desc="MSSQL Server Activity Extract Script")
    cred_manager = create_credential_manager(PRODUCT_NAME, creds_file)
    mssql_settings = cred_manager.get_credentials("mssql")
    auth_type = mssql_settings.get("auth_type", "sql_authentication")
    server_name = mssql_settings.get("server", "")
    try:

        # TODO: get the last time the profiler was executed
        # For now, we'll default to None, but this will eventually need
        # input from a scheduler component.
        last_execution_time = None
        mode = "overwrite"

        # Extract activity metrics
        logger.info(f"Extracting activity metrics for: {server_name}")
        print(f"Extracting activity metrics for: {server_name}")
        connection = get_sqlserver_reader(
            mssql_settings, db_name="master", server_name=server_name, auth_type=auth_type
        )

        # Query stats
        table_name = "query_stats"
        table_query = MSSQLQueries.get_query_stats(last_execution_time)
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_resultset_to_db(result, f"mssql_{table_name}", db_path, mode=mode)

        # Stored procedure stats
        table_name = "proc_stats"
        table_query = MSSQLQueries.get_procedure_stats(last_execution_time)
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_resultset_to_db(result, f"mssql_{table_name}", db_path, mode=mode)

        # Session info
        table_name = "sessions"
        table_query = MSSQLQueries.get_sessions(last_execution_time)
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_resultset_to_db(result, f"mssql_{table_name}", db_path, mode=mode)

        # CPU Utilization
        table_name = "cpu_utilization"
        table_query = MSSQLQueries.get_cpu_utilization(last_execution_time)
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_resultset_to_db(result, f"mssql_{table_name}", db_path, mode=mode)

        print(json.dumps({"status": "success", "message": "All data loaded successfully loaded successfully"}))

    except Exception as e:
        logger.error(f"Failed to extract activity info for SQL server: {str(e)}")
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
