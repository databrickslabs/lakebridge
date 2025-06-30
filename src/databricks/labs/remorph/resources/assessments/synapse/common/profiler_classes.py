import pandas as pd
from azure.monitor.query import MetricAggregationType
from datetime import datetime, timedelta, time as timex
from azure.synapse.artifacts import models as ArtifactsModels

from abc import ABC


# AzureArtifact
class AzureArtifact(ABC):
    # constructor
    def __init__(self, tz_info, artifacts_client, fetch_batch_size=20, max_pages=5000):
        self.tz_info = tz_info
        self.client = artifacts_client
        self.fetch_batch_size = fetch_batch_size
        self.max_pages = max_pages

    @staticmethod
    def project_dict(obj, keep=[], remove=[]):
        """
        Utility Function to keep or/and remove fields
        """
        return {k: v for (k, v) in obj.items() if (not keep or k.lower() in keep) and (k.lower() not in remove)}

    @staticmethod
    def create_run_filter_parameters(last_updated_after, last_updated_before):
        # TODO: fix this from global context to import
        return ArtifactsModels.RunFilterParameters(
            last_updated_after=last_updated_after, last_updated_before=last_updated_before
        )

    # fetch_from_iter
    def fetch_from_iter(self, iterator, keep, remove):
        """
        Creates item groups ( lists of max size fetch_batch_size) from input iterator
        """
        group = []
        for entry in iterator:
            # add item to group
            group.append(AzureArtifact.project_dict(entry.as_dict(), keep, remove))
            # yield the group list for every batch size
            if len(group) >= self.fetch_batch_size:
                yield group
                # make sure to clear the group after yield
                group.clear()
        # make sure to emit eny partial groups after looping
        if len(group) > 0:
            yield group

    # query_activity_runs
    def query_activity_runs(self, runs_query, run_filter_parameters, keep, remove):
        """
        Creates item groups ( lists of max size fetch_batch_size) from the input query after executing it using run_filter_parameters
        query response here has pagination logic. It should have following fields
          i)  value : list of items (current page)
          ii) continuation_token: The continuation token for getting the next page of results, if any remaining results exist, null otherwise.

          Example Model:
            https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.models.pipelinerunsqueryresponse?view=azure-python-preview
        """
        group = []
        page_count = 0
        run_filter_parameters.continuation_token = None
        while page_count == 0 or (run_filter_parameters.continuation_token and page_count <= self.max_pages):
            page_result = runs_query(filter_parameters=run_filter_parameters)
            page_count += 1
            for entry in page_result.value:
                # add item to group
                group.append(AzureArtifact.project_dict(entry.as_dict(), keep, remove))
                # yield the group list for every batch size
                if len(group) >= self.fetch_batch_size:
                    yield group
                    # make sure to clear the group after yield
                    group.clear()
            # update continuation_token for run_filter_parameters
            run_filter_parameters.continuation_token = page_result.continuation_token
        # make sure to emit eny partial groups after outer looping
        if len(group) > 0:
            yield group


"""
  SynapseWorkspace
"""


class SynapseWorkspace(AzureArtifact):
    """
    constructor

    :artifacts_client - create one using *get_synapse_artifacts_client* utility function

    Refereces:
      - https://learn.microsoft.com/en-us/python/api/azure-synapse/azure.synapse?view=azure-python-preview

    """

    # constructor
    def __init__(self, tz_info, artifacts_client, fetch_batch_size=20):
        super().__init__(tz_info, artifacts_client, fetch_batch_size)

    # get_workspace_info
    def get_workspace_info(
        self,
        keep=[
            'id',
            'name',
            'type',
            'workspace_uid',
            'location',
            'provisioning_state',
            'default_data_lake_storage',
            'workspace_repository_configuration',
            'purview_configuration',
            'extra_properties',
        ],
        remove=[],
    ):
        """
        Query workspace info
        """
        workspace = self.client.workspace.get()
        return AzureArtifact.project_dict(workspace.as_dict(), keep, remove)

    # list_sql_pools
    def list_sql_pools(
        self, keep=['id', 'name', 'type', 'location', 'sku', 'provisioning_state', 'status', 'creation_date'], remove=[]
    ):
        """
        Query SQL Pools
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.sqlpoolsoperations?view=azure-python-preview#azure-synapse-artifacts-operations-sqlpoolsoperations-list
        """
        result = self.client.sql_pools.list()  # returns SqlPoolInfoListResult
        yield from self.fetch_from_iter(result.value, keep, remove)

    # list_bigdata_pools
    def list_bigdata_pools(self, keep=[], remove=[]):
        """
        Query Spark Pools
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.bigdatapoolsoperations?view=azure-python-preview#azure-synapse-artifacts-operations-bigdatapoolsoperations-list
        """
        result = self.client.big_data_pools.list()  # returns BigDataPoolResourceInfoListResult
        yield from self.fetch_from_iter(result.value, keep, remove)

    # list_linked_services
    def list_linked_services(self, keep=[], remove=[]):
        """
        Query Pipe Lines
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.linkedserviceoperations?view=azure-python-preview#azure-synapse-artifacts-operations-linkedserviceoperations-get-linked-services-by-workspace
        """
        result = (
            self.client.linked_service.get_linked_services_by_workspace()
        )  # returns ItemPaged[LinkedServiceResource]
        yield from self.fetch_from_iter(result, keep, remove)

    # list_data_flows
    def list_data_flows(self, keep=[], remove=[]):
        """
        Query Data Flows
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.dataflowoperations?view=azure-python-preview#azure-synapse-artifacts-operations-dataflowoperations-get-data-flows-by-workspace

        """
        result = self.client.data_flow.get_data_flows_by_workspace()  # returns ItemPaged[DataFlowResource]
        yield from self.fetch_from_iter(result, keep, remove)

    # list_pipelines
    def list_pipelines(self, keep=[], remove=[]):
        """
        Query Pipelines
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.pipelineoperations?view=azure-python-preview#azure-synapse-artifacts-operations-pipelineoperations-get-pipelines-by-workspace
        """
        result = self.client.pipeline.get_pipelines_by_workspace()  # returns ItemPaged[PipelineResource]
        yield from self.fetch_from_iter(result, keep, remove)

    # list_notebooks
    def list_notebooks(self, keep=[], remove=[]):
        """
        Query Noteboos
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.notebookoperations?view=azure-python-preview#azure-synapse-artifacts-operations-notebookoperations-get-notebooks-by-workspace
        """
        result = self.client.notebook.get_notebooks_by_workspace()  # returns ItemPaged[NotebookResource]
        yield from self.fetch_from_iter(result, keep, remove)

    # list_spark_job_definitions
    def list_spark_job_definitions(self, keep=[], remove=[]):
        """
        Query Spark Jobs
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.sparkjobdefinitionoperations?view=azure-python-preview#azure-synapse-artifacts-operations-sparkjobdefinitionoperations-get-spark-job-definitions-by-workspace
        """
        result = (
            self.client.spark_job_definition.get_spark_job_definitions_by_workspace()
        )  # returns ItemPaged[SparkJobDefinitionResource]
        yield from self.fetch_from_iter(result, keep, remove)

    # list_sqlscripts
    def list_sqlscripts(self, keep=[], remove=[]):
        """
        Query Pipe Lines
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.sqlscriptoperations?view=azure-python-preview#azure-synapse-artifacts-operations-sqlscriptoperations-get-sql-scripts-by-workspace
        """
        result = self.client.sql_script.get_sql_scripts_by_workspace()  # rerurns ItemPaged[SqlScriptResource]
        yield from self.fetch_from_iter(result, keep, remove)

        # list_triggers

    def list_triggers(self, keep=[], remove=[]):
        """
        Query triggers
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.triggeroperations?view=azure-python-preview#azure-synapse-artifacts-operations-triggeroperations-get-triggers-by-workspace
        """
        result = self.client.trigger.get_triggers_by_workspace()  # returns ItemPaged[TriggerResource]
        yield from self.fetch_from_iter(result, keep, remove)

        # list_libraries

    def list_libraries(self, keep=[], remove=[]):
        """
        Query Libraries
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.libraryoperations?view=azure-python-preview#azure-synapse-artifacts-operations-libraryoperations-list
        """
        result = self.client.library.list()  # returns ItemPaged[LibraryResource]
        yield from self.fetch_from_iter(result, keep, remove)

        # list_datasets

    def list_datasets(self, keep=[], remove=[]):
        """
        Query Pipe Lines
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.datasetoperations?view=azure-python-preview#azure-synapse-artifacts-operations-datasetoperations-get-datasets-by-workspace
        """
        result = self.client.dataset.get_datasets_by_workspace()  # returns ItemPaged[DatasetResource]
        yield from self.fetch_from_iter(result, keep, remove)

    # list_pipeline_runs_dep
    def list_pipeline_runs_dep(self, run_filter_params, keep=[], remove=[]):
        """
        Query Pipe Lines
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.pipelinerunoperations?view=azure-python-preview#azure-synapse-artifacts-operations-pipelinerunoperations-query-pipeline-runs-by-workspace
        """
        result = self.client.pipeline_run.query_pipeline_runs_by_workspace(
            filter_parameters=run_filter_params
        )  # returns PipelineRunsQueryResponse

        for run in result.value:
            yield AzureArtifact.project_dict(run.as_dict(), keep, remove)

    # list_pipeline_runs
    def list_pipeline_runs(self, last_updated_date, keep=[], remove=[]):
        """
        Query Pipeline runs by last_updted_date (in UTC)
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.pipelinerunoperations?view=azure-python-preview#azure-synapse-artifacts-operations-pipelinerunoperations-query-pipeline-runs-by-workspace
        """
        last_updated_after = datetime.combine(last_updated_date, timex(0, 0, 0, 0)).replace(tzinfo=self.tz_info)
        last_updated_before = datetime.combine(last_updated_date, timex(23, 59, 59, 999999)).replace(
            tzinfo=self.tz_info
        )
        print(f"INFO: Running {self.__class__.__name__}::list_pipeline_runs with last_updated_date={last_updated_date}")

        # set RunFilterParameters
        run_filter_params = AzureArtifact.create_run_filter_parameters(last_updated_after, last_updated_before)
        runs_query = self.client.pipeline_run.query_pipeline_runs_by_workspace
        yield from self.query_activity_runs(runs_query, run_filter_params, keep, remove)

    # list_trigger_runs
    def list_trigger_runs(self, last_updated_date, keep=[], remove=[]):
        """
        Query Pipe Lines
        SDK Reference:
          https://learn.microsoft.com/en-us/python/api/azure-synapse-artifacts/azure.synapse.artifacts.operations.triggerrunoperations?view=azure-python-preview#azure-synapse-artifacts-operations-triggerrunoperations-query-trigger-runs-by-workspace
        """
        last_updated_after = datetime.combine(last_updated_date, timex(0, 0, 0, 0)).replace(tzinfo=self.tz_info)
        last_updated_before = datetime.combine(last_updated_date, timex(23, 59, 59, 999999)).replace(
            tzinfo=self.tz_info
        )
        print(f"INFO: Running {self.__class__.__name__}::list_trigger_runs with last_updated_date={last_updated_date}")

        # set RunFilterParameters
        run_filter_params = AzureArtifact.create_run_filter_parameters(last_updated_after, last_updated_before)
        runs_query = self.client.trigger_run.query_trigger_runs_by_workspace
        yield from self.query_activity_runs(runs_query, run_filter_params, keep, remove)


# SynapseMetrics
class SynapseMetrics:
    def __init__(self, metrics_client, num_days=90, granularity_mins=15, fetch_batch_size=500, max_pages=5000):
        self.client = metrics_client
        self.num_days = num_days
        self.granularity_mins = granularity_mins
        self.fetch_batch_size = fetch_batch_size
        self.max_pages = max_pages

    # fetch_metrics
    def fetch_metrics(self, metrics):
        """
        Creates item groups ( lists of max size fetch_batch_size) from input iterator
        """
        data = []
        for metric in metrics:
            for ts_entry in metric.timeseries:
                for metric_value in ts_entry.data:
                    # add item to group
                    data.append(
                        {
                            "name": metric.name,
                            "timestamp": metric_value.timestamp,
                            "average": metric_value.average,
                            "count": metric_value.count,
                            "maximum": metric_value.maximum,
                            "minimum": metric_value.minimum,
                            "total": metric_value.total,
                        }
                    )
        return pd.DataFrame(data)

    # get_dedicated_pool_metrics
    def get_dedicated_sql_pool_metrics(self, resource_id):
        """
        Quries metrics for a specific dedicated sql metric
        resource_id: input sql pool resource id
        """
        response = self.client.query_resource(
            resource_id,
            metric_names=[
                "DWULimit",
                "DWUUsed",
                "DWUUsedPercent",
                "MemoryUsedPercent",
                "CPUPercent",
                "Connections",
                "ActiveQueries",
            ],
            timespan=timedelta(days=self.num_days),
            granularity=timedelta(minutes=self.granularity_mins),
            aggregations=[
                MetricAggregationType.AVERAGE,
                MetricAggregationType.COUNT,
                MetricAggregationType.MINIMUM,
                MetricAggregationType.MAXIMUM,
                MetricAggregationType.TOTAL,
            ],
        )
        # Fetch Metrics
        return self.fetch_metrics(response.metrics)

    # get_spark_pool_metrics
    def get_spark_pool_metrics(self, resource_id):
        """
        Query metrics for a specific spark pool
        resource_id: input spark pool resource id
        """
        response = self.client.query_resource(
            resource_id,
            metric_names=[
                "BigDataPoolApplicationsEnded",
                "BigDataPoolAllocatedCores",
                "BigDataPoolAllocatedMemory",
                "BigDataPoolApplicationsActive",
            ],
            timespan=timedelta(days=self.num_days),
            granularity=timedelta(minutes=self.granularity_mins),
            aggregations=[
                MetricAggregationType.AVERAGE,
                MetricAggregationType.COUNT,
                MetricAggregationType.MINIMUM,
                MetricAggregationType.MAXIMUM,
                MetricAggregationType.TOTAL,
            ],
        )
        # Fetch Metrics
        return self.fetch_metrics(response.metrics)

    # get_workspace_level_metrics
    def get_workspace_level_metrics(self, resource_id):
        """
        Query Workspace level metrics
        resource_id: input workspace resource id
        """
        response = self.client.query_resource(
            resource_id,
            metric_names=[
                "IntegrationActivityRunsEnded",
                "IntegrationPipelineRunsEnded",
                "IntegrationTriggerRunsEnded",
                "BuiltinSqlPoolDataProcessedBytes",
                "BuiltinSqlPoolLoginAttempts",
                "BuiltinSqlPoolRequestsEnded",
            ],
            timespan=timedelta(days=self.num_days),
            granularity=timedelta(hours=1),
            aggregations=[
                MetricAggregationType.AVERAGE,
                MetricAggregationType.COUNT,
                MetricAggregationType.MINIMUM,
                MetricAggregationType.MAXIMUM,
                MetricAggregationType.TOTAL,
            ],
        )
        # Fetch Metrics and return as DataFrame
        return self.fetch_metrics(response.metrics)
