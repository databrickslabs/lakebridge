import json
import sys
import logging
import urllib3
import zoneinfo
import pandas as pd

from databricks.labs.remorph.resources.assessments.synapse.common.profiler_classes import (
    SynapseWorkspace,
    SynapseMetrics,
)
from databricks.labs.remorph.resources.assessments.synapse.common.functions import (
    arguments_loader,
    insert_df_to_duckdb,
    get_config,
    get_azure_metrics_query_client,
    get_synapse_artifacts_client,
)


def execute():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

    db_path, creds_file = arguments_loader(desc="Monitoring Metrics Extract Script")

    try:
        synapse_workspace_settings = get_config(creds_file)["synapse"]
        synapse_profiler_settings = synapse_workspace_settings["profiler"]

        tz_info = synapse_workspace_settings["workspace"]["tz_info"]
        workspace_tz = zoneinfo.ZoneInfo(tz_info)
        workspace_name = synapse_workspace_settings["workspace"]["name"]
        logger.info(f"workspace_name → {workspace_name}")

        artifacts_client = get_synapse_artifacts_client(synapse_workspace_settings)
        workspace = SynapseWorkspace(workspace_tz, artifacts_client)
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        metrics_client = get_azure_metrics_query_client()
        synapse_metrics = SynapseMetrics(metrics_client)

        workspace_info = workspace.get_workspace_info()
        print(workspace_info)

        if not "id" in workspace_info:
            raise ValueError("ERROR: Missing Workspace ID for extracting Workspace Level Metrics")
        workspace_resource_id = workspace_info["id"]
        logger.info(f"workspace_resource_id  →  {workspace_resource_id}")
        metrics_df = synapse_metrics.get_workspace_level_metrics(workspace_resource_id)
        insert_df_to_duckdb(metrics_df, db_path, "workspace_level_metrics")

        # SQL Pool Metrics

        exclude_dedicated_sql_pools = synapse_profiler_settings.get("exclude_dedicated_sql_pools", None)
        dedicated_sql_pools_profiling_list = synapse_profiler_settings.get("dedicated_sql_pools_profiling_list", None)

        logger.info(f" exclude_dedicated_sql_pools        →  {exclude_dedicated_sql_pools}")
        logger.info(f" dedicated_sql_pools_profiling_list →  {dedicated_sql_pools_profiling_list}")

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

        if exclude_dedicated_sql_pools:
            logger.info(
                f" exclude_dedicated_sql_pools is set to {exclude_dedicated_sql_pools}, Skipping metrics extract for Dedicated SQL pools"
            )
        else:
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
            step_name = "dedicated_pool_metrics"
            print(f"Loading data for {step_name}")
            insert_df_to_duckdb(pools_df, db_path, step_name)

        # Spark Pool  Metrics

        exclude_spark_pools = synapse_profiler_settings.get("exclude_spark_pools", None)
        spark_pools_profiling_list = synapse_profiler_settings.get("spark_pools_profiling_list", None)

        logger.info(f" exclude_spark_pools        →  {exclude_spark_pools}")
        logger.info(f" spark_pools_profiling_list →  {spark_pools_profiling_list}")

        if exclude_spark_pools:
            logger.info(
                f" exclude_spark_pools is set to {exclude_spark_pools}, Skipping metrics extract for Spark pools"
            )
        else:
            spark_pools_iter = workspace.list_bigdata_pools()
            all_spark_pool_list = [pool for poolPages in spark_pools_iter for pool in poolPages]
            spark_pools_to_profile = (
                all_spark_pool_list
                if not spark_pools_profiling_list
                else [pool for pool in all_spark_pool_list if pool['name'] in spark_pools_profiling_list]
            )

            logger.info(f" Pool names to extract metrics: {[entry['name'] for entry in spark_pools_to_profile]}")

        if exclude_spark_pools:
            logger.info(
                f" exclude_spark_pools is set to {exclude_spark_pools}, Skipping metrics extract for Spark pools"
            )
        else:
            spark_pools_df = pd.DataFrame()
            for idx, pool in enumerate(spark_pools_to_profile):
                pool_name = pool['name']
                pool_resoure_id = pool['id']

                logger.info(f"{'*'*70}")
                logger.info(f"{idx+1}) Pool Name: {pool_name}")
                logger.info(f"   Resource id: {pool_resoure_id}")

                step_name = "spark_pool_metrics"

                spark_pool_metrics_df = synapse_metrics.get_dedicated_sql_pool_metrics(pool_resoure_id)
                if idx == 0:
                    spark_pools_df = spark_pool_metrics_df
                else:
                    spark_pools_df = pools_df.union(pool_metrics_df)

            # Insert the combined metrics into DuckDB
            insert_df_to_duckdb(pools_df, db_path, step_name)
            logger.info(">End")

        # This is the output format expected by the pipeline.py which orchestrates the execution of this script
        print(json.dumps({"status": "success", "message": "Data loaded successfully"}))

    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
