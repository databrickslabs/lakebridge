import json
import sys
import urllib3
import zoneinfo
import pandas as pd

from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.resources.assessments.synapse.common.profiler_classes import (
    SynapseWorkspace,
    SynapseMetrics,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.functions import (
    arguments_loader,
    create_azure_metrics_query_client,
    create_synapse_artifacts_client,
    set_logger,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.duckdb_helpers import insert_df_to_duckdb


def execute():
    logger = set_logger(__name__)

    db_path, creds_file = arguments_loader(desc="Monitoring Metrics Extract Script")
    cred_manager = create_credential_manager(PRODUCT_NAME, creds_file)
    synapse_workspace_settings = cred_manager.get_credentials("synapse")
    synapse_profiler_settings = synapse_workspace_settings["profiler"]

    tz_info = synapse_workspace_settings["workspace"]["tz_info"]
    workspace_tz = zoneinfo.ZoneInfo(tz_info)
    workspace_name = synapse_workspace_settings["workspace"]["name"]
    logger.info(f"workspace_name: {workspace_name}")

    artifacts_client = create_synapse_artifacts_client(synapse_workspace_settings)

    try:

        workspace = SynapseWorkspace(workspace_tz, artifacts_client)
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        metrics_client = create_azure_metrics_query_client()
        synapse_metrics = SynapseMetrics(metrics_client)

        workspace_info = workspace.get_workspace_info()
        logger.info(f"workspace info: {workspace_info}")

        if "id" not in workspace_info:
            raise ValueError("ERROR: Missing Workspace ID for extracting Workspace Level Metrics")
        workspace_resource_id = workspace_info["id"]
        logger.info(f"workspace_resource_id : {workspace_resource_id}")
        metrics_df = synapse_metrics.get_workspace_level_metrics(workspace_resource_id)
        insert_df_to_duckdb(metrics_df, db_path, "metrics_workspace_level_metrics")

        # SQL Pool Metrics

        exclude_dedicated_sql_pools = synapse_profiler_settings.get("exclude_dedicated_sql_pools", None)
        dedicated_sql_pools_profiling_list = synapse_profiler_settings.get("dedicated_sql_pools_profiling_list", None)

        logger.info(f" exclude_dedicated_sql_pools: {exclude_dedicated_sql_pools}")
        logger.info(f" dedicated_sql_pools_profiling_list: {dedicated_sql_pools_profiling_list}")

        if exclude_dedicated_sql_pools:
            logger.info(
                f" exclude_dedicated_sql_pools is set to {exclude_dedicated_sql_pools}, Skipping metrics extract for Dedicated SQL pools"
            )
        else:
            dedicated_sqlpools = workspace.list_sql_pools()
            all_dedicated_pools_list = [pool for poolPages in dedicated_sqlpools for pool in poolPages]
            dedicated_pools_to_profile = (
                all_dedicated_pools_list
                if not dedicated_sql_pools_profiling_list
                else [pool for pool in all_dedicated_pools_list if pool['name'] in dedicated_sql_pools_profiling_list]
            )
            msg = f"Pool names to extract metrics: {[entry['name'] for entry in dedicated_pools_to_profile]}"
            logger.info(msg)

            pools_df = pd.DataFrame()
            for idx, pool in enumerate(dedicated_pools_to_profile):
                pool_name = pool['name']
                pool_resoure_id = pool['id']

                logger.info(f"{'*'*70}")
                logger.info(f"{idx+1}) Pool Name: {pool_name}")
                logger.info(f"   Resource id: {pool_resoure_id}")
                print(f"{idx+1}) Pool Name: {pool_name}")
                print(f"   Resource id: {pool_resoure_id}")

                pool_metrics_df = synapse_metrics.get_dedicated_sql_pool_metrics(pool_resoure_id)
                if idx == 0:
                    pools_df = pool_metrics_df
                else:
                    pools_df = pools_df.union(pool_metrics_df)

            # Insert the combined metrics into DuckDB
            step_name = "metrics_dedicated_pool_metrics"
            print(f"Loading data for {step_name}")
            insert_df_to_duckdb(pools_df, db_path, step_name)

        # Spark Pool  Metrics

        exclude_spark_pools = synapse_profiler_settings.get("exclude_spark_pools", None)
        spark_pools_profiling_list = synapse_profiler_settings.get("spark_pools_profiling_list", None)

        logger.info(f" exclude_spark_pools       : {exclude_spark_pools}")
        logger.info(f" spark_pools_profiling_list: {spark_pools_profiling_list}")

        if exclude_spark_pools:
            logger.info(
                f" exclude_spark_pools is set to {exclude_spark_pools}, Skipping metrics extract for Spark pools"
            )
        else:
            print("Starting Execution for Spark Pools Metrics Extraction")
            spark_pools_iter = workspace.list_bigdata_pools()
            all_spark_pool_list = [pool for poolPages in spark_pools_iter for pool in poolPages]
            spark_pools_to_profile = (
                all_spark_pool_list
                if not spark_pools_profiling_list
                else [pool for pool in all_spark_pool_list if pool['name'] in spark_pools_profiling_list]
            )

            logger.info(f" Pool names to extract metrics: {[entry['name'] for entry in spark_pools_to_profile]}")
            print(f" Pool names to extract metrics: {[entry['name'] for entry in spark_pools_to_profile]}")

            spark_pools_df = pd.DataFrame()
            for idx, pool in enumerate(spark_pools_to_profile):
                pool_name = pool['name']
                pool_resoure_id = pool['id']

                logger.info(f"{'*'*70}")
                logger.info(f"{idx+1}) Pool Name: {pool_name}")
                logger.info(f"   Resource id: {pool_resoure_id}")

                step_name = "metrics_spark_pool_metrics"

                spark_pool_metrics_df = synapse_metrics.get_spark_pool_metrics(pool_resoure_id)
                if idx == 0:
                    spark_pools_df = spark_pool_metrics_df
                else:
                    spark_pools_df = spark_pools_df.union(spark_pool_metrics_df)

            # Insert the combined metrics into DuckDB
            insert_df_to_duckdb(spark_pools_df, db_path, step_name)

        # This is the output format expected by the pipeline.py which orchestrates the execution of this script
        print(json.dumps({"status": "success", "message": "Data loaded successfully"}))

    except Exception as e:
        logger.error(f"Failed to extract info for Synapse Monitoring Metrics: {str(e)}")
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
