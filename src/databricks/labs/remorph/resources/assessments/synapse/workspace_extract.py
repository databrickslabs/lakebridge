import json
import sys
import logging
from datetime import date, timedelta
import zoneinfo
import pandas as pd
from databricks.labs.remorph.resources.assessments.synapse.common.functions import (
    arguments_loader,
    insert_df_to_duckdb,
    get_config,
    get_synapse_artifacts_client,
)
from databricks.labs.remorph.resources.assessments.synapse.common.profiler_classes import SynapseWorkspace


def execute():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

    db_path, creds_file = arguments_loader(desc="Workspace Extract")

    try:
        # Initialize workspace settings and client
        synapse_workspace_settings = get_config(creds_file)["synapse"]

        tz_info = synapse_workspace_settings["workspace"]["tz_info"]
        workspace_tz = zoneinfo.ZoneInfo(tz_info)
        workspace_name = synapse_workspace_settings["workspace"]["name"]
        print(f"workspace_name → {workspace_name}")
        logger.info(f"workspace_name → {workspace_name}")

        artifacts_client = get_synapse_artifacts_client(synapse_workspace_settings)
        workspace = SynapseWorkspace(workspace_tz, artifacts_client)

        # Extract workspace info
        print("Starting Workspace Info")
        workspace_info = workspace.get_workspace_info()
        workspace_info_df = pd.json_normalize(workspace_info)
        insert_df_to_duckdb(workspace_info_df, db_path, "workspace_info")

        # Extract SQL pools
        sql_pools = workspace.list_sql_pools()
        sql_pools_df = pd.json_normalize([pool for pool_pages in sql_pools for pool in pool_pages])
        insert_df_to_duckdb(sql_pools_df, db_path, "sql_pools")

        # Extract Spark pools
        spark_pools = workspace.list_bigdata_pools()
        spark_pools_df = pd.json_normalize([pool for pool_pages in spark_pools for pool in pool_pages])
        insert_df_to_duckdb(spark_pools_df, db_path, "spark_pools")

        # Extract Linked Services
        linked_services = workspace.list_linked_services()
        linked_services_df = pd.json_normalize(linked_services)
        insert_df_to_duckdb(linked_services_df, db_path, "linked_services")

        # Extract Data Flows
        dataflows = workspace.list_data_flows()
        dataflows_df = pd.json_normalize(dataflows)
        insert_df_to_duckdb(dataflows_df, db_path, "dataflows")

        # Extract Pipelines
        pipelines = workspace.list_pipelines()
        pipelines_df = pd.json_normalize(pipelines)
        insert_df_to_duckdb(pipelines_df, db_path, "pipelines")

        # Extract Spark Jobs
        spark_jobs = workspace.list_spark_job_definitions()
        spark_jobs_df = pd.json_normalize(spark_jobs)
        insert_df_to_duckdb(spark_jobs_df, db_path, "spark_jobs")

        # Extract Notebooks
        notebooks = workspace.list_notebooks()
        notebooks_df = pd.json_normalize(notebooks)
        insert_df_to_duckdb(notebooks_df, db_path, "notebooks")

        # Extract SQL Scripts
        sql_scripts = workspace.list_sqlscripts()
        sql_scripts_df = pd.json_normalize(sql_scripts)
        insert_df_to_duckdb(sql_scripts_df, db_path, "sql_scripts")

        # Extract Triggers
        triggers = workspace.list_triggers()
        triggers_df = pd.json_normalize(triggers)
        insert_df_to_duckdb(triggers_df, db_path, "triggers")

        # Extract Libraries
        libraries = workspace.list_libraries()
        libraries_df = pd.json_normalize(libraries)
        insert_df_to_duckdb(libraries_df, db_path, "libraries")

        # Extract Datasets
        datasets = workspace.list_datasets()
        datasets_df = pd.json_normalize(datasets)
        insert_df_to_duckdb(datasets_df, db_path, "datasets")

        # Extract Pipeline Runs (last 60 days)
        today = date.today()
        pipeline_runs_list = []
        for days in range(1, 60):
            last_upd = today + timedelta(days=-days)
            pipeline_runs = workspace.list_pipeline_runs(last_upd)
            for run in pipeline_runs:
                run['last_upd'] = last_upd
                pipeline_runs_list.append(run)
        pipeline_runs_df = pd.json_normalize(pipeline_runs_list)

        insert_df_to_duckdb(pipeline_runs_df, db_path, "pipeline_runs")

        # Extract Trigger Runs (last 60 days)
        trigger_runs_list = []
        for days in range(1, 60):
            last_upd = today + timedelta(days=-days)
            trigger_runs = workspace.list_trigger_runs(last_upd)
            for run in trigger_runs:
                run['last_upd'] = last_upd
                trigger_runs_list.append(run)
        trigger_runs_df = pd.json_normalize(trigger_runs_list)
        insert_df_to_duckdb(trigger_runs_df, db_path, "trigger_runs")

        # This is the output format expected by the pipeline.py which orchestrates the execution of this script
        print(json.dumps({"status": "success", "message": "Data loaded successfully"}))

    except Exception as e:

        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
