import json
import sys
import logging
from databricks.labs.remorph.resources.assessments.synapse.common.functions import (
    arguments_loader,
    get_config,
    get_synapse_artifacts_client,
    save_resultset_to_db,
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

    db_path, creds_file = arguments_loader(desc="Synapse Synapse Dedicated SQL Pool Extract Script")

    cred_manager = create_credential_manager(creds_file)
    config = cred_manager.get_credentials("synapse")["workspace"]

    try:
        synapse_workspace_settings = get_config(creds_file)["synapse"]
        synapse_profiler_settings = synapse_workspace_settings["profiler"]

        tz_info = synapse_workspace_settings["workspace"]["tz_info"]
        workspace_tz = zoneinfo.ZoneInfo(tz_info)
        exclude_dedicated_sql_pools = synapse_profiler_settings.get("exclude_dedicated_sql_pools", None)
        dedicated_sql_pools_profiling_list = synapse_profiler_settings.get("dedicated_sql_pools_profiling_list", None)
        artifacts_client = get_synapse_artifacts_client(synapse_workspace_settings)
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
        for idx, entry in enumerate(dedicated_pools_to_profile):
            print(f"\t {idx+1}  {entry['name'].ljust(50, '.')}{entry['status']}")

        live_dedicated_pools_to_profile = [entry for entry in dedicated_pools_to_profile if entry['status'] == 'Online']
        logger.info(f"live_dedicated_pools_to_profile: {[entry['name'] for entry in live_dedicated_pools_to_profile]}")

        # Info: Extract
        for idx, entry in enumerate(live_dedicated_pools_to_profile):
            entry_info = f"{entry['name']} [{entry['status']}]"
            print(f"{idx:02d})  {entry_info.ljust(60, '.')} : RUNNING extract...")
            logger.info(f"{idx:02d})  {entry_info.ljust(60, '.')} : RUNNING extract...")

            mode = "overwrite" if idx == 0 else "append"

            # tables
            table_query = SynapseQueries.list_tables()
            connection = get_sqlpool_reader(config, entry['name'])
            logger.info("Loading 'tables' for pool: %s", entry['name'])
            print(f"Loading 'tables' for pool: {entry['name']}")
            result = connection.execute(text(table_query))
            save_resultset_to_db(result, "tables", db_path, mode=mode)

            # columns
            column_query = SynapseQueries.list_columns()
            logger.info("Loading 'columns' for pool: %s", entry['name'])
            print(f"Loading 'columns' for pool: {entry['name']}")
            result = connection.execute(text(column_query))
            save_resultset_to_db(result, "columns", db_path, mode=mode)

            # views
            view_query = SynapseQueries.list_views()
            logger.info("Loading 'views' for pool: %s", entry['name'])
            print(f"Loading 'views' for pool: {entry['name']}")
            result = connection.execute(text(view_query))
            save_resultset_to_db(result, "views", db_path, mode=mode)

            # routines
            routine_query = SynapseQueries.list_routines()
            logger.info("Loading 'routines' for pool: %s", entry['name'])
            print(f"Loading 'routines' for pool: {entry['name']}")
            result = connection.execute(text(routine_query))
            save_resultset_to_db(result, "routines", db_path, mode=mode)

            # storage_info
            storage_info_query = SynapseQueries.get_db_storage_info()
            logger.info("Loading 'storage_info' for pool: %s", entry['name'])
            print(f"Loading 'storage_info' for pool: {entry['name']}")
            result = connection.execute(text(storage_info_query))
            save_resultset_to_db(result, "storage_info", db_path, mode=mode)

        # Activity: Extract
        sqlpool_names_to_profile = ",".join([entry['name'] for entry in live_dedicated_pools_to_profile])
        msg = f"Running 04_dedicated_sqlpools_activity_extract with sqlpool_names  → [{sqlpool_names_to_profile}] ..."
        logger.info(msg)
        print(sqlpool_names_to_profile)
        sqlpool_names_to_profile_list = [
            entry for entry in sqlpool_names_to_profile.strip().split(",") if len(entry.strip())
        ]
        for idx, sqlpool_name in enumerate(sqlpool_names_to_profile_list):
            print(f"INFO: sqlpool_name → {sqlpool_name}")
            connection = get_sqlpool_reader(config, sqlpool_name)

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

        print(json.dumps({"status": "success", "message": " All data loaded successfully loaded successfully"}))

    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
