import json
import sys

from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.resources.assessments.mssql.common.connector import get_sqlserver_reader
from databricks.labs.lakebridge.resources.assessments.mssql.common.functions import create_msql_sql_client
from databricks.labs.lakebridge.resources.assessments.mssql.common.queries import MSSQLQueries
from databricks.labs.lakebridge.resources.assessments.synapse.common.duckdb_helpers import save_resultset_to_db
from databricks.labs.lakebridge.resources.assessments.synapse.common.functions import arguments_loader, set_logger


def execute():
    logger = set_logger(__file__)

    db_path, creds_file = arguments_loader(desc="MSSQL Server Info Extract Script")
    cred_manager = create_credential_manager(PRODUCT_NAME, creds_file)
    mssql_settings = cred_manager.get_credentials("mssql")
    config = mssql_settings["workspace"]
    auth_type = mssql_settings["jdbc"].get("auth_type", "sql_authentication")
    mssql_profiler_settings = mssql_settings["profiler"]
    mssql_client = create_msql_sql_client(mssql_profiler_settings)

    try:
        # list all the SQL servers in the subscription
        sql_servers = mssql_client.servers.list()
        for idx, sql_server in enumerate(sql_servers):

            mode = "overwrite" if idx == 0 else "append"

            # Extract info metrics
            server_name = sql_server.name
            logger.info(f"Extracting info metrics for: {server_name}")
            print(f"Extracting info metrics for: {server_name}")
            fully_qualified_domain = sql_server.fully_qualified_domain_name
            connection = get_sqlserver_reader(
                config, db_name="master", fully_qualified_domain_name=fully_qualified_domain, auth_type=auth_type
            )

            # System info
            table_name = "sys_info"
            table_query = MSSQLQueries.get_sys_info()
            logger.info(f"Loading '{table_name}' for SQL server: %s", server_name)
            print(f"Loading '{table_name}' for SQL server: %s", server_name)
            result = connection.fetch(table_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # Databases
            table_name = "databases"
            table_query = MSSQLQueries.get_databases()
            logger.info(f"Loading '{table_name}' for SQL server: %s", server_name)
            # TODO: if list of `db_names` not provided in config
            # then loop through all the databases to collect the following info
            result = connection.fetch(table_query)
            db_name = "main"
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # Tables
            table_name = "tables"
            table_query = MSSQLQueries.get_tables(db_name)
            logger.info(f"Loading '{table_name}' for SQL server: %s", server_name)
            result = connection.fetch(table_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # Views
            table_name = "views"
            table_query = MSSQLQueries.get_views(db_name)
            logger.info(f"Loading '{table_name}' for SQL server: %s", server_name)
            result = connection.fetch(table_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # Columns
            table_name = "columns"
            table_query = MSSQLQueries.get_columns(db_name)
            logger.info(f"Loading '{table_name}' for SQL server: %s", server_name)
            result = connection.fetch(table_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # Indexed views
            table_name = "indexed_views"
            table_query = MSSQLQueries.get_indexed_views(db_name)
            logger.info(f"Loading '{table_name}' for SQL server: %s", server_name)
            result = connection.fetch(table_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # Routines
            table_name = "routines"
            table_query = MSSQLQueries.get_routines(db_name)
            logger.info(f"Loading '{table_name}' for SQL server: %s", server_name)
            result = connection.fetch(table_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # Database sizes
            table_name = "db_sizes"
            table_query = MSSQLQueries.get_db_sizes(db_name)
            logger.info(f"Loading '{table_name}' for SQL server: %s", server_name)
            result = connection.fetch(table_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # Table sizes
            table_name = "table_sizes"
            table_query = MSSQLQueries.get_table_sizes(db_name)
            logger.info(f"Loading '{table_name}' for SQL server: %s", server_name)
            result = connection.fetch(table_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            print(json.dumps({"status": "success", "message": "All data loaded successfully loaded successfully"}))

    except Exception as e:
        logger.error(f"Failed to execute info extract for SQL server: {str(e)}")
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
