import json
import sys
from datetime import date, timedelta
import zoneinfo
import pandas as pd

from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.assessments import PRODUCT_NAME

from databricks.labs.lakebridge.resources.assessments.synapse.common.functions import (
    arguments_loader,
    create_synapse_artifacts_client,
    set_logger,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.duckdb_helpers import insert_df_to_duckdb
from databricks.labs.lakebridge.resources.assessments.synapse.common.profiler_classes import SynapseWorkspace


def execute():
    logger = set_logger(__name__)

    db_path, creds_file = arguments_loader(desc="Workspace Extract")

    cred_manager = create_credential_manager(PRODUCT_NAME, creds_file)
    synapse_workspace_settings = cred_manager.get_credentials("synapse")
    tz_info = synapse_workspace_settings["workspace"]["tz_info"]
    workspace_tz = zoneinfo.ZoneInfo(tz_info)
    workspace_name = synapse_workspace_settings["workspace"]["name"]

    logger.info(f"workspace_name: {workspace_name}")

    artifacts_client = create_synapse_artifacts_client(synapse_workspace_settings)

    try:
        # Initialize workspace settings and client
        workspace = SynapseWorkspace(workspace_tz, artifacts_client)

        # Extract workspace info
        table_name = "workspace_workspace_info"
        logger.info(f"Extraction started for {table_name}")
        workspace_info = workspace.get_workspace_info()
        workspace_info_df = pd.json_normalize(workspace_info)
        insert_df_to_duckdb(workspace_info_df, db_path, table_name)

        # Extract SQL pools
        table_name = "workspace_sql_pools"
        logger.info(f"Extraction started for {table_name}")
        sql_pools = workspace.list_sql_pools()
        sql_pools_df = pd.json_normalize([pool for pool_pages in sql_pools for pool in pool_pages])
        insert_df_to_duckdb(sql_pools_df, db_path, table_name)

        # Extract Spark pools
        table_name = "workspace_spark_pools"
        logger.info(f"Extraction started for {table_name}")
        spark_pools = workspace.list_bigdata_pools()
        spark_pools_df = pd.json_normalize([pool for pool_pages in spark_pools for pool in pool_pages])
        insert_df_to_duckdb(spark_pools_df, db_path, table_name)

        # Extract Linked Services
        table_name = "workspace_linked_services"
        logger.info(f"Extraction started for {table_name}")
        linked_services = workspace.list_linked_services()
        linked_services_df = pd.json_normalize(linked_services)
        insert_df_to_duckdb(linked_services_df, db_path, table_name)

        # Extract Data Flows
        table_name = "workspace_dataflows"
        logger.info(f"Extraction started for {table_name}")
        dataflows = workspace.list_data_flows()
        dataflows_df = pd.json_normalize(dataflows)
        insert_df_to_duckdb(dataflows_df, db_path, table_name)

        # Extract Pipelines
        table_name = "workspace_pipelines"
        logger.info(f"Extraction started for {table_name}")
        pipelines = workspace.list_pipelines()
        pipelines_df = pd.json_normalize(pipelines)
        insert_df_to_duckdb(pipelines_df, db_path, table_name)

        # Extract Spark Jobs
        table_name = "workspace_spark_jobs"
        logger.info(f"Extraction started for {table_name}")
        spark_jobs = workspace.list_spark_job_definitions()
        spark_jobs_df = pd.json_normalize(spark_jobs)
        insert_df_to_duckdb(spark_jobs_df, db_path, table_name)

        # Extract Notebooks
        table_name = "workspace_notebooks"
        logger.info(f"Extraction started for {table_name}")
        notebooks = workspace.list_notebooks()
        notebooks_df = pd.json_normalize(notebooks)
        insert_df_to_duckdb(notebooks_df, db_path, table_name)

        # Extract SQL Scripts
        table_name = "workspace_sql_scripts"
        logger.info(f"Extraction started for {table_name}")
        sql_scripts = workspace.list_sqlscripts()
        sql_scripts_df = pd.json_normalize(sql_scripts)
        insert_df_to_duckdb(sql_scripts_df, db_path, table_name)

        # Extract Triggers
        table_name = "workspace_triggers"
        logger.info(f"Extraction started for {table_name}")
        triggers = workspace.list_triggers()
        triggers_df = pd.json_normalize(triggers)
        insert_df_to_duckdb(triggers_df, db_path, table_name)

        # Extract Libraries
        table_name = "workspace_libraries"
        logger.info(f"Extraction started for {table_name}")
        libraries = workspace.list_libraries()
        libraries_df = pd.json_normalize(libraries)
        insert_df_to_duckdb(libraries_df, db_path, table_name)

        # Extract Datasets
        table_name = "workspace_datasets"
        logger.info(f"Extraction started for {table_name}")
        datasets = workspace.list_datasets()
        datasets_df = pd.json_normalize(datasets)
        insert_df_to_duckdb(datasets_df, db_path, table_name)

        # Extract Pipeline Runs (last 60 days)
        today = date.today()
        pipeline_runs_list = []
        for days in range(1, 60):
            table_name = "workspace_pipeline_runs"
            logger.info(f"Extraction started for {table_name} for date: {days}")
            last_upd = today + timedelta(days=-days)
            pipeline_runs = workspace.list_pipeline_runs(last_upd)
            if not pipeline_runs:
                logger.warning(f"No pipeline runs found for {last_upd}")
                continue
            if not all(isinstance(run, dict) for run in pipeline_runs):
                logger.error(f"Unexpected data export in {table_name}: {pipeline_runs}")
                raise ValueError(f"Invalid data export in {table_name}")
            for run in pipeline_runs:
                run['last_upd'] = last_upd
                pipeline_runs_list.append(run)

        pipeline_runs_df = pd.json_normalize(pipeline_runs_list)

        insert_df_to_duckdb(pipeline_runs_df, db_path, table_name)

        # Extract Trigger Runs (last 60 days)
        trigger_runs_list = []
        for days in range(1, 60):
            table_name = "workspace_trigger_runs"
            logger.info(f"Extraction started for {table_name} for date: {days}")
            last_upd = today + timedelta(days=-days)
            trigger_runs = workspace.list_trigger_runs(last_upd)
            if not trigger_runs:
                logger.warning(f"No trigger runs found for {last_upd}")
                continue
            if not all(isinstance(run, dict) for run in trigger_runs):
                logger.error(f"Unexpected data export in {table_name}: {trigger_runs}")
                raise ValueError(f"Invalid data export in {table_name}")
            for run in trigger_runs:
                run['last_upd'] = last_upd
                trigger_runs_list.append(run)
        trigger_runs_df = pd.json_normalize(trigger_runs_list)
        insert_df_to_duckdb(trigger_runs_df, db_path, table_name)

        # This is the output format expected by the pipeline.py which orchestrates the execution of this script
        print(json.dumps({"status": "success", "message": "Data loaded successfully"}))

    except Exception as e:
        logger.error(f"Failed to extract info for Synapse Workspace Info: {str(e)}")
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
