import json
import sys
import duckdb

from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.assessments import PRODUCT_NAME

from databricks.labs.lakebridge.resources.assessments.synapse.common.functions import (
    arguments_loader,
    set_logger,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.duckdb_helpers import (
    save_resultset_to_db,
    get_max_column_value_duckdb,
)

from databricks.labs.lakebridge.resources.assessments.synapse.common.queries import SynapseQueries
from databricks.labs.lakebridge.resources.assessments.synapse.common.connector import get_sqlpool_reader


def get_serverless_database_groups(
    db_path,
    inclusion_list=None,
    exclusion_list=None,
    table_name="serverless_databases",
):
    """
    Reads serverless databases from DuckDB, groups by collation_name,
    and applies inclusion/exclusion filters.

    Returns:
        (serverless_database_groups, serverless_database_groups_in_scope)
    """
    with duckdb.connect(db_path) as conn:
        rows = conn.execute(f"SELECT name, collation_name FROM {table_name}").fetchall()

    serverless_database_groups = {}
    for name, collation_name in rows:
        serverless_database_groups.setdefault(collation_name, []).append(name)

    inclusion_list = inclusion_list or []
    exclusion_list = exclusion_list or []

    serverless_database_groups_in_scope = {}
    for collation_name, dbs in serverless_database_groups.items():
        for db in dbs:
            if (not inclusion_list or db in inclusion_list) and db not in exclusion_list:
                serverless_database_groups_in_scope.setdefault(collation_name, []).append(db)

    return serverless_database_groups_in_scope


def execute():
    logger = set_logger(__name__)
    db_path, creds_file = arguments_loader(desc="Synapse Synapse Serverless SQL Pool Extract Script")

    cred_manager = create_credential_manager(PRODUCT_NAME, creds_file)
    synapse_workspace_settings = cred_manager.get_credentials("synapse")
    config = synapse_workspace_settings["workspace"]
    auth_type = synapse_workspace_settings["jdbc"].get("auth_type", "sql_authentication")
    synapse_profiler_settings = synapse_workspace_settings["profiler"]

    connection = None
    try:
        if not synapse_profiler_settings.get("exclude_serverless_sql_pool", False):
            # Databases
            database_query = SynapseQueries.list_databases()
            connection = get_sqlpool_reader(
                config,
                'master',
                endpoint_key='serverless_sql_endpoint',
                auth_type=auth_type,
            )
            result = connection.fetch(database_query)
            save_resultset_to_db(result, "serverless_databases", db_path, mode="overwrite")

            serverless_database_groups_in_scope = get_serverless_database_groups(db_path)
            logger.info(f"serverless db in scope: {serverless_database_groups_in_scope}")

            for idx, collation_name in enumerate(serverless_database_groups_in_scope):
                mode = "overwrite" if idx == 0 else "append"
                databases = serverless_database_groups_in_scope[collation_name]

                for db_name in databases:
                    connection = get_sqlpool_reader(
                        config,
                        db_name,
                        endpoint_key='serverless_sql_endpoint',
                        auth_type=auth_type,
                    )
                    # tables
                    table_name = 'serverless_tables'
                    table_query = SynapseQueries.list_tables(db_name)
                    logger.info(f"Loading '{table_name}' for pool: %s", db_name)
                    result = connection.fetch(table_query)
                    save_resultset_to_db(result, table_name, db_path, mode=mode)

                    # columns
                    table_name = "serverless_columns"
                    column_query = SynapseQueries.list_columns(db_name)
                    logger.info(f"Loading '{table_name}' for pool: %s", db_name)
                    result = connection.fetch(column_query)
                    save_resultset_to_db(result, table_name, db_path, mode=mode)

                    # views
                    table_name = "serverless_views"
                    view_query = SynapseQueries.list_views(db_name)
                    logger.info(f"Loading '{table_name}' for pool: %s", db_name)
                    result = connection.fetch(view_query)
                    save_resultset_to_db(result, table_name, db_path, mode=mode)

                    # Routines
                    table_name = "serverless_routines"
                    view_query = SynapseQueries.list_routines(db_name, True)
                    logger.info(f"Loading '{table_name}' for pool: %s", db_name)
                    result = connection.fetch(view_query)
                    save_resultset_to_db(result, table_name, db_path, mode=mode)

                    mode = "append"

            pool_name = "serverless"
            # Data Processed
            table_name = "serverless_data_processed"
            logger.info(f"Loading '{table_name}' for pool: %s", pool_name)
            data_processed_query = SynapseQueries.data_processed(pool_name)

            session_result = connection.fetch(data_processed_query)
            save_resultset_to_db(session_result, table_name, db_path, mode="append")

            # Activity Extract:
            table_name = "serverless_sessions"
            logger.info(f"Loading '{table_name}' for pool: %s", pool_name)
            prev_max_login_time = get_max_column_value_duckdb("login_time", table_name, db_path)
            session_query = SynapseQueries.list_serverless_sessions(pool_name, prev_max_login_time)

            session_result = connection.fetch(session_query)
            save_resultset_to_db(session_result, table_name, db_path, mode="append")

            table_name = "serverless_session_requests"
            logger.info(f"Loading '{table_name}' for pool: %s", pool_name)
            prev_max_end_time = get_max_column_value_duckdb("start_time", table_name, db_path)
            session_request_query = SynapseQueries.list_serverless_requests(pool_name, prev_max_end_time)

            session_request_result = connection.fetch(session_request_query)
            save_resultset_to_db(session_request_result, table_name, db_path, mode="append")

            table_name = "serverless_query_stats"
            logger.info(f"Loading '{table_name}' for pool: %s", pool_name)
            max_last_execution_time = get_max_column_value_duckdb("last_execution_time", table_name, db_path)
            query_stats = SynapseQueries.list_query_stats(max_last_execution_time)

            session_result = connection.fetch(query_stats)
            save_resultset_to_db(session_result, table_name, db_path, mode="append")

            table_name = "serverless_requests_history"
            logger.info(f"Loading '{table_name}' for pool: %s", pool_name)
            max_end_time = get_max_column_value_duckdb("end_time", table_name, db_path)
            query_history = SynapseQueries.query_requests_history(max_end_time)

            session_request_result = connection.fetch(query_history)
            save_resultset_to_db(session_request_result, table_name, db_path, mode="append")

        else:
            logger.info("'exclude_serverless_sql_pool' configuration is set to True.Skipping Serverless Pool extracts.")

        print(json.dumps({"status": "success", "message": "Data loaded successfully"}))

    except Exception as e:
        logger.error(f"Failed to extract info for Synapse Serverless SQL Pool: {str(e)}")
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
