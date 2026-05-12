import json
import sys

from databricks.labs.blueprint.entrypoint import get_logger

from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.resources.assessments.common.cli import arguments_loader
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb
from databricks.labs.lakebridge.resources.assessments.mssql.common.connector import get_sqlserver_reader
from databricks.labs.lakebridge.resources.assessments.mssql.common.queries import MSSQLQueries
from databricks.labs.lakebridge.resources.assessments.mssql.common.schemas import MSSQL_SCHEMAS

logger = get_logger(__file__)


def execute():
    db_path, creds_file = arguments_loader(desc="MSSQL Server Info Extract Script")
    cred_manager = create_credential_manager(PRODUCT_NAME, creds_file)
    mssql_settings = cred_manager.get_credentials("mssql")
    auth_type = mssql_settings.get("auth_type", "sql_authentication")
    server_name = mssql_settings.get("server", "")

    try:
        # TODO: get the last time the profiler was executed
        # For now, we'll default to None, but this will eventually need
        # input from a scheduler component.
        mode = "overwrite"

        # Extract info metrics
        logger.info(f"Extracting info metrics for: {server_name}")
        print(f"Extracting info metrics for: {server_name}")
        connection = get_sqlserver_reader(
            mssql_settings, db_name="master", server_name=server_name, auth_type=auth_type
        )

        # System info
        table_name = "sys_info"
        table_query = MSSQLQueries.get_sys_info()
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        print(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_to_duckdb(
            result.to_df(), f"mssql_{table_name}", db_path, mode=mode, schema=MSSQL_SCHEMAS[f"mssql_{table_name}"]
        )

        # Databases
        table_name = "databases"
        table_query = MSSQLQueries.get_databases()
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_to_duckdb(
            result.to_df(), f"mssql_{table_name}", db_path, mode=mode, schema=MSSQL_SCHEMAS[f"mssql_{table_name}"]
        )

        # Tables
        table_name = "tables"
        table_query = MSSQLQueries.get_tables()
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_to_duckdb(
            result.to_df(), f"mssql_{table_name}", db_path, mode=mode, schema=MSSQL_SCHEMAS[f"mssql_{table_name}"]
        )

        # Views
        table_name = "views"
        table_query = MSSQLQueries.get_views()
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_to_duckdb(
            result.to_df(), f"mssql_{table_name}", db_path, mode=mode, schema=MSSQL_SCHEMAS[f"mssql_{table_name}"]
        )

        # Columns
        table_name = "columns"
        table_query = MSSQLQueries.get_columns()
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_to_duckdb(
            result.to_df(), f"mssql_{table_name}", db_path, mode=mode, schema=MSSQL_SCHEMAS[f"mssql_{table_name}"]
        )

        # Indexed views
        table_name = "indexed_views"
        table_query = MSSQLQueries.get_indexed_views()
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_to_duckdb(
            result.to_df(), f"mssql_{table_name}", db_path, mode=mode, schema=MSSQL_SCHEMAS[f"mssql_{table_name}"]
        )

        # Routines
        table_name = "routines"
        table_query = MSSQLQueries.get_routines()
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_to_duckdb(
            result.to_df(), f"mssql_{table_name}", db_path, mode=mode, schema=MSSQL_SCHEMAS[f"mssql_{table_name}"]
        )

        # Database sizes
        table_name = "db_sizes"
        table_query = MSSQLQueries.get_db_sizes()
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_to_duckdb(
            result.to_df(), f"mssql_{table_name}", db_path, mode=mode, schema=MSSQL_SCHEMAS[f"mssql_{table_name}"]
        )

        # Table sizes
        table_name = "table_sizes"
        table_query = MSSQLQueries.get_table_sizes()
        logger.info(f"Loading '{table_name}' for SQL server: {server_name}")
        result = connection.fetch(table_query)
        save_to_duckdb(
            result.to_df(), f"mssql_{table_name}", db_path, mode=mode, schema=MSSQL_SCHEMAS[f"mssql_{table_name}"]
        )

        print(json.dumps({"status": "success", "message": "All data loaded successfully loaded successfully"}))

    except Exception as e:
        logger.error(f"Failed to execute info extract for SQL server: {str(e)}")
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
