import json
import sys
from databricks.labs.lakebridge.resources.assessments.synapse.common.functions import (
    arguments_loader,
    create_synapse_artifacts_client,
    set_logger,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.duckdb_helpers import (
    save_resultset_to_db,
    get_max_column_value_duckdb,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.connector import get_sqlpool_reader

import zoneinfo
from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.resources.assessments.synapse.common.profiler_classes import SynapseWorkspace
from databricks.labs.lakebridge.resources.assessments.synapse.common.queries import SynapseQueries


def execute():
    logger = set_logger(__file__)

    db_path, creds_file = arguments_loader(desc="Synapse Synapse Dedicated SQL Pool Extract Script")

    cred_manager = create_credential_manager(PRODUCT_NAME, creds_file)
    synapse_workspace_settings = cred_manager.get_credentials("synapse")
    config = synapse_workspace_settings["workspace"]
    auth_type = synapse_workspace_settings["jdbc"].get("auth_type", "sql_authentication")
    synapse_profiler_settings = synapse_workspace_settings["profiler"]

    tz_info = synapse_workspace_settings["workspace"]["tz_info"]
    workspace_tz = zoneinfo.ZoneInfo(tz_info)
    exclude_dedicated_sql_pools = synapse_profiler_settings.get("exclude_dedicated_sql_pools", None)
    dedicated_sql_pools_profiling_list = synapse_profiler_settings.get("dedicated_sql_pools_profiling_list", None)
    artifacts_client = create_synapse_artifacts_client(synapse_workspace_settings)

    connection = None

    try:
        workspace = SynapseWorkspace(workspace_tz, artifacts_client)

        if exclude_dedicated_sql_pools:
            msg = f"exclude_dedicated_sql_pools is set to {exclude_dedicated_sql_pools}, Skipping metrics extract for Dedicated SQL pools"
            logger.info(msg)
            print(json.dumps({"status": "success", "message": msg}))
            return

        dedicated_sqlpools = workspace.list_sql_pools()
        all_dedicated_pools_list = [pool for poolPages in dedicated_sqlpools for pool in poolPages]
        if dedicated_sql_pools_profiling_list:
            dedicated_pools_to_profile = [
                pool for pool in all_dedicated_pools_list if pool['name'] in dedicated_sql_pools_profiling_list
            ]
        else:
            dedicated_pools_to_profile = all_dedicated_pools_list

        logger.info("Pool names to extract metrics...")

        live_dedicated_pools_to_profile = [entry for entry in dedicated_pools_to_profile if entry['status'] == 'Online']
        logger.info(f"live_dedicated_pools_to_profile: {[entry['name'] for entry in live_dedicated_pools_to_profile]}")

        # Info: Extract
        for idx, entry in enumerate(live_dedicated_pools_to_profile):
            entry_info = f"{entry['name']} [{entry['status']}]"
            logger.info(f"{idx:02d})  {entry_info.ljust(60, '.')} : RUNNING extract...")

            mode = "overwrite" if idx == 0 else "append"
            pool_name = entry['name']
            # tables
            table_name = "dedicated_tables"
            table_query = SynapseQueries.list_tables(pool_name)
            connection = get_sqlpool_reader(config, pool_name, auth_type=auth_type)
            logger.info(f"Loading '{table_name}' for pool: %s", pool_name)
            result = connection.fetch(table_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # columns
            table_name = "dedicated_columns"
            column_query = SynapseQueries.list_columns(pool_name)
            logger.info(f"Loading '{table_name}' for pool: %s", pool_name)
            result = connection.fetch(column_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # views
            table_name = "dedicated_views"
            view_query = SynapseQueries.list_views(pool_name)
            logger.info(f"Loading '{table_name}' for pool: %s", pool_name)
            result = connection.fetch(view_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # routines
            table_name = "dedicated_routines"
            routine_query = SynapseQueries.list_routines(pool_name)
            logger.info(f"Loading '{table_name}' for pool: %s", pool_name)
            result = connection.fetch(routine_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

            # storage_info
            table_name = "dedicated_storage_info"
            storage_info_query = SynapseQueries.get_db_storage_info(pool_name)
            logger.info(f"Loading '{table_name}' for pool: %s", pool_name)
            result = connection.fetch(storage_info_query)
            save_resultset_to_db(result, table_name, db_path, mode=mode)

        # Activity: Extract
        sqlpool_names_to_profile = ",".join([entry['name'] for entry in live_dedicated_pools_to_profile])
        msg = f"Running 04_dedicated_sqlpools_activity_extract with sqlpool_names :[{sqlpool_names_to_profile}] ..."
        logger.info(msg)

        sqlpool_names_to_profile_list = [
            entry for entry in sqlpool_names_to_profile.strip().split(",") if len(entry.strip())
        ]
        for idx, sqlpool_name in enumerate(sqlpool_names_to_profile_list):
            # print(f"INFO: sqlpool_name:{sqlpool_name}")
            connection = get_sqlpool_reader(config, sqlpool_name, auth_type=auth_type)

            table_name = "dedicated_sessions"
            prev_max_login_time = get_max_column_value_duckdb("login_time", table_name, db_path)
            session_query = SynapseQueries.list_dedicated_sessions(
                pool_name=sqlpool_name, last_login_time=prev_max_login_time
            )

            session_result = connection.fetch(session_query)
            save_resultset_to_db(session_result, table_name, db_path, mode="append")

            table_name = "dedicated_session_requests"
            prev_max_end_time = get_max_column_value_duckdb("end_time", table_name, db_path)
            session_request_query = SynapseQueries.list_dedicated_requests(prev_max_end_time)

            session_request_result = connection.fetch(session_request_query)
            save_resultset_to_db(session_request_result, table_name, db_path, mode="append")

        print(json.dumps({"status": "success", "message": " All data loaded successfully loaded successfully"}))

    except Exception as e:
        logger.error(f"Failed to extract info for Synapse Dedicated SQL Pool: {str(e)}")
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
