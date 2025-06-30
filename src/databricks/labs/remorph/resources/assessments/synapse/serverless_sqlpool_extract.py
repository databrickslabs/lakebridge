import json
import sys
import logging
from databricks.labs.remorph.resources.assessments.synapse.common.functions import (
    arguments_loader,
    get_config,
    get_synapse_artifacts_client,
    save_resultset_to_db,
    get_serverless_database_groups,
    get_max_column_value_duckdb,
)
import zoneinfo
from databricks.labs.remorph.resources.assessments.synapse.common.profiler_classes import SynapseWorkspace
from databricks.labs.remorph.resources.assessments.synapse.common.queries import SynapseQueries
from databricks.labs.remorph.resources.assessments.synapse.common.connector import (
    create_credential_manager,
    get_sqlpool_reader,
)
from sqlalchemy import text


def execute():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    db_path, creds_file = arguments_loader(desc="Synapse Synapse Serverless SQL Pool Extract Script")

    cred_manager = create_credential_manager(creds_file)
    config = cred_manager.get_credentials("synapse")["workspace"]

    try:
        synapse_workspace_settings = get_config(creds_file)["synapse"]
        synapse_profiler_settings = synapse_workspace_settings["profiler"]
        print(synapse_workspace_settings)
        tz_info = synapse_workspace_settings["workspace"]["tz_info"]
        workspace_tz = zoneinfo.ZoneInfo(tz_info)
        artifacts_client = get_synapse_artifacts_client(synapse_workspace_settings)
        workspace = SynapseWorkspace(workspace_tz, artifacts_client)

        if not synapse_profiler_settings.get("exclude_serverless_sql_pool", False):

            # Databases
            database_query = SynapseQueries.list_databases()
            connection = get_sqlpool_reader(config, 'master', 'serverless_sql_endpoint')
            logger.info("Loading 'tables' for pool: %s", 'master')
            print(f"Loading 'tables' for pool: 'master'")
            result = connection.execute(text(database_query))
            save_resultset_to_db(result, "serverless_databases", db_path, mode="overwrite")

            serverless_database_groups_in_scope = get_serverless_database_groups(db_path)
            print("*************************************")
            print(serverless_database_groups_in_scope)

            for idx, collation_name in enumerate(serverless_database_groups_in_scope):
                mode = "overwrite" if idx == 0 else "append"
                databases = serverless_database_groups_in_scope[collation_name]

                for db_name in databases:
                    connection = get_sqlpool_reader(config, db_name, 'serverless_sql_endpoint')
                    # tables
                    table_name = 'serverless_tables'
                    table_query = SynapseQueries.list_tables()
                    logger.info(f"Loading '{table_name}' for pool: %s", db_name)
                    print(f"Loading '{table_name}' for pool: {db_name}")
                    result = connection.execute(text(table_query))
                    save_resultset_to_db(result, table_name, db_path, mode=mode)

                    # columns
                    table_name = "serverless_columns"
                    column_query = SynapseQueries.list_columns()
                    logger.info(f"Loading '{table_name}' for pool: %s", db_name)
                    print(f"Loading '{table_name}' for pool: {db_name}")
                    result = connection.execute(text(column_query))
                    save_resultset_to_db(result, table_name, db_path, mode=mode)

                    # views
                    table_name = "serverless_views"
                    view_query = SynapseQueries.list_views()
                    logger.info(f"Loading '{table_name}' for pool: %s", db_name)
                    print(f"Loading '{table_name}' for pool: {db_name}")
                    result = connection.execute(text(view_query))
                    save_resultset_to_db(result, table_name, db_path, mode=mode)

                    # routines
                    table_name = "serverless_routines"
                    routine_query = SynapseQueries.list_routines()
                    logger.info(f"Loading '{table_name}' for pool: %s", db_name)
                    print(f"Loading '{table_name}' for pool: {db_name}")
                    result = connection.execute(text(routine_query))
                    save_resultset_to_db(result, table_name, db_path, mode=mode)

                    mode = "append"

            # Activity Extract:
            table_name = "sessions"
            prev_max_login_time = get_max_column_value_duckdb("login_time", table_name, db_path)
            session_query = SynapseQueries.list_sessions(prev_max_login_time)

            session_result = connection.execute(text(session_query))
            save_resultset_to_db(session_result, table_name, db_path, mode="append")

            table_name = "session_request"
            prev_max_end_time = get_max_column_value_duckdb("end_time", table_name, db_path)
            session_request_query = SynapseQueries.list_requests(prev_max_end_time)

            session_request_result = connection.execute(text(session_request_query))
            save_resultset_to_db(session_request_result, table_name, db_path, mode="append")

            table_name = "serverless_query_stats"
            max_last_execution_time = get_max_column_value_duckdb("last_execution_time", table_name, db_path)
            query_stats = SynapseQueries.list_query_stats(max_last_execution_time)

            session_result = connection.execute(text(query_stats))
            save_resultset_to_db(session_result, table_name, db_path, mode="append")

            table_name = "serverless_requests_history"
            max_end_time = get_max_column_value_duckdb("end_time", table_name, db_path)
            query_history = SynapseQueries.query_requests_history(max_end_time)

            session_request_result = connection.execute(text(query_history))
            save_resultset_to_db(session_request_result, table_name, db_path, mode="append")

        else:
            print(
                'INFO: "exclude_serverless_sql_pool" configuration is set to True. Skipping Serverless Pool extracts.'
            )

        print(json.dumps({"status": "success", "message": "Data loaded successfully"}))

    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
