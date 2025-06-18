import logging
import sys
import os
from datetime import datetime
from uuid import uuid4

from pyspark.errors import PySparkException
from pyspark.sql import DataFrame, SparkSession
from sqlglot import Dialect

from databricks.labs.lakebridge.config import (
    DatabaseConfig,
    TableRecon,
    ReconcileConfig,
    ReconcileMetadataConfig,
)
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.compare import (
    capture_mismatch_data_and_columns,
    reconcile_data,
    join_aggregate_data,
    reconcile_agg_data_per_rule,
)
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.source_adapter import create_adapter
from databricks.labs.lakebridge.reconcile.exception import (
    DataSourceRuntimeException,
    InvalidInputException,
    ReconciliationException,
)
from databricks.labs.lakebridge.reconcile.query_builder.aggregate_query import AggregateQueryBuilder
from databricks.labs.lakebridge.reconcile.query_builder.count_query import CountQueryBuilder
from databricks.labs.lakebridge.reconcile.query_builder.hash_query import HashQueryBuilder
from databricks.labs.lakebridge.reconcile.query_builder.sampling_query import (
    SamplingQueryBuilder,
)
from databricks.labs.lakebridge.reconcile.query_builder.threshold_query import (
    ThresholdQueryBuilder,
)
from databricks.labs.lakebridge.reconcile.recon_capture import (
    ReconCapture,
    generate_final_reconcile_output,
    ReconIntermediatePersist,
    generate_final_reconcile_aggregate_output,
)
from databricks.labs.lakebridge.reconcile.recon_config import (
    Schema,
    Table,
    AggregateQueryRules,
    SamplingOptions,
    RECONCILE_OPERATION_NAME,
    AGG_RECONCILE_OPERATION_NAME,
)
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    DataReconcileOutput,
    ReconcileOutput,
    ReconcileProcessDuration,
    SchemaReconcileOutput,
    ThresholdOutput,
    ReconcileRecordCount,
    AggregateQueryOutput,
)
from databricks.labs.lakebridge.reconcile.sampler import SamplerFactory
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
from databricks.labs.lakebridge.transpiler.execute import verify_workspace_client
from databricks.sdk import WorkspaceClient
from databricks.labs.blueprint.installation import Installation
from databricks.connect import DatabricksSession

logger = logging.getLogger(__name__)
_SAMPLE_ROWS = 50


def validate_input(input_value: str, list_of_value: set, message: str):
    if input_value not in list_of_value:
        error_message = f"{message} --> {input_value} is not one of {list_of_value}"
        logger.error(error_message)
        raise InvalidInputException(error_message)


def main(*argv) -> None:
    logger.debug(f"Arguments received: {argv}")

    assert len(sys.argv) == 2, f"Invalid number of arguments: {len(sys.argv)}," f" Operation name must be specified."
    operation_name = sys.argv[1]

    assert operation_name in {
        RECONCILE_OPERATION_NAME,
        AGG_RECONCILE_OPERATION_NAME,
    }, f"Invalid option: {operation_name}"

    w = WorkspaceClient()

    installation = Installation.assume_user_home(w, "lakebridge")

    reconcile_config = installation.load(ReconcileConfig)

    catalog_or_schema = (
        reconcile_config.database_config.source_catalog
        if reconcile_config.database_config.source_catalog
        else reconcile_config.database_config.source_schema
    )
    filename = f"recon_config_{reconcile_config.data_source}_{catalog_or_schema}_{reconcile_config.report_type}.json"

    logger.info(f"Loading {filename} from Databricks Workspace...")

    table_recon = installation.load(type_ref=TableRecon, filename=filename)

    if operation_name == AGG_RECONCILE_OPERATION_NAME:
        return _trigger_reconcile_aggregates(w, table_recon, reconcile_config)

    return _trigger_recon(w, table_recon, reconcile_config)


def _trigger_recon(
    w: WorkspaceClient,
    table_recon: TableRecon,
    reconcile_config: ReconcileConfig,
):
    try:
        recon_output = recon(
            ws=w,
            spark=DatabricksSession.builder.getOrCreate(),
            table_recon=table_recon,
            reconcile_config=reconcile_config,
        )
        logger.info(f"recon_output: {recon_output}")
        logger.info(f"recon_id: {recon_output.recon_id}")
    except ReconciliationException as e:
        logger.error(f"Error while running recon: {e.reconcile_output}")
        raise e


def _trigger_reconcile_aggregates(
    ws: WorkspaceClient,
    table_recon: TableRecon,
    reconcile_config: ReconcileConfig,
):
    """
    Triggers the reconciliation process for aggregated data  between source and target tables.
    Supported Aggregate functions: MIN, MAX, COUNT, SUM, AVG, MEAN, MODE, PERCENTILE, STDDEV, VARIANCE, MEDIAN

    This function attempts to reconcile aggregate data based on the configurations provided. It logs the outcome
    of the reconciliation process, including any errors encountered during execution.

    Parameters:
    - ws (WorkspaceClient): The workspace client used to interact with Databricks workspaces.
    - table_recon (TableRecon): Configuration for the table reconciliation process, including source and target details.
    - reconcile_config (ReconcileConfig): General configuration for the reconciliation process,
                                                                    including database and table settings.

    Raises:
    - ReconciliationException: If an error occurs during the reconciliation process, it is caught and re-raised
      after logging the error details.
    """
    try:
        recon_output = reconcile_aggregates(
            ws=ws,
            spark=DatabricksSession.builder.getOrCreate(),
            table_recon=table_recon,
            reconcile_config=reconcile_config,
        )
        logger.info(f"recon_output: {recon_output}")
        logger.info(f"recon_id: {recon_output.recon_id}")
    except ReconciliationException as e:
        logger.error(f"Error while running aggregate reconcile: {str(e)}")
        raise e


def recon(
    ws: WorkspaceClient,
    spark: SparkSession,
    table_recon: TableRecon,
    reconcile_config: ReconcileConfig,
    local_test_run: bool = False,
) -> ReconcileOutput:
    """[EXPERIMENTAL] Reconcile the data between the source and target tables."""
    # verify the workspace client and add proper product and version details
    # TODO For now we are utilising the
    #  verify_workspace_client from transpile/execute.py file. Later verify_workspace_client function has to be
    #  refactored

    ws_client: WorkspaceClient = verify_workspace_client(ws)

    # validate the report type
    report_type = reconcile_config.report_type.lower()
    logger.info(f"report_type: {report_type}, data_source: {reconcile_config.data_source} ")
    validate_input(report_type, {"schema", "data", "row", "all"}, "Invalid report type")

    source, target = initialise_data_source(
        engine=get_dialect(reconcile_config.data_source),
        spark=spark,
        ws=ws_client,
        secret_scope=reconcile_config.secret_scope,
    )

    recon_id = str(uuid4())
    # initialise the Reconciliation
    reconciler = Reconciliation(
        source,
        target,
        reconcile_config.database_config,
        report_type,
        SchemaCompare(spark=spark),
        get_dialect(reconcile_config.data_source),
        spark,
        metadata_config=reconcile_config.metadata_config,
    )

    # initialise the recon capture class
    recon_capture = ReconCapture(
        database_config=reconcile_config.database_config,
        recon_id=recon_id,
        report_type=report_type,
        source_dialect=get_dialect(reconcile_config.data_source),
        ws=ws_client,
        spark=spark,
        metadata_config=reconcile_config.metadata_config,
        local_test_run=local_test_run,
    )

    for table_conf in table_recon.tables:
        recon_process_duration = ReconcileProcessDuration(start_ts=str(datetime.now()), end_ts=None)
        schema_reconcile_output = SchemaReconcileOutput(is_valid=True)
        data_reconcile_output = DataReconcileOutput()
        try:
            src_schema, tgt_schema = _get_schema(
                source=source, target=target, table_conf=table_conf, database_config=reconcile_config.database_config
            )
        except DataSourceRuntimeException as e:
            schema_reconcile_output = SchemaReconcileOutput(is_valid=False, exception=str(e))
        else:
            if report_type in {"schema", "all"}:
                schema_reconcile_output = _run_reconcile_schema(
                    reconciler=reconciler, table_conf=table_conf, src_schema=src_schema, tgt_schema=tgt_schema
                )
                logger.warning("Schema comparison is completed.")

            if report_type in {"data", "row", "all"}:
                data_reconcile_output = _run_reconcile_data(
                    reconciler=reconciler, table_conf=table_conf, src_schema=src_schema, tgt_schema=tgt_schema
                )
                logger.warning(f"Reconciliation for '{report_type}' report completed.")

        recon_process_duration.end_ts = str(datetime.now())
        # Persist the data to the delta tables
        recon_capture.start(
            data_reconcile_output=data_reconcile_output,
            schema_reconcile_output=schema_reconcile_output,
            table_conf=table_conf,
            recon_process_duration=recon_process_duration,
            record_count=reconciler.get_record_count(table_conf, report_type),
        )
        if report_type != "schema":
            ReconIntermediatePersist(
                spark=spark, path=generate_volume_path(table_conf, reconcile_config.metadata_config)
            ).clean_unmatched_df_from_volume()

    return _verify_successful_reconciliation(
        generate_final_reconcile_output(
            recon_id=recon_id,
            spark=spark,
            metadata_config=reconcile_config.metadata_config,
            local_test_run=local_test_run,
        )
    )


def _verify_successful_reconciliation(
    reconcile_output: ReconcileOutput, operation_name: str = "reconcile"
) -> ReconcileOutput:
    for table_output in reconcile_output.results:
        if table_output.exception_message or (
            table_output.status.column is False
            or table_output.status.row is False
            or table_output.status.schema is False
            or table_output.status.aggregate is False
        ):
            raise ReconciliationException(
                f" Reconciliation failed for one or more tables. Please check the recon metrics for more details."
                f" **{operation_name}** failed.",
                reconcile_output=reconcile_output,
            )

    logger.info("Reconciliation completed successfully.")
    return reconcile_output


def generate_volume_path(table_conf: Table, metadata_config: ReconcileMetadataConfig):
    catalog = metadata_config.catalog
    schema = metadata_config.schema
    return f"/Volumes/{catalog}/{schema}/{metadata_config.volume}/{table_conf.source_name}_{table_conf.target_name}/"


def initialise_data_source(
    ws: WorkspaceClient,
    spark: SparkSession,
    engine: Dialect,
    secret_scope: str,
):
    source = create_adapter(engine=engine, spark=spark, ws=ws, secret_scope=secret_scope)
    target = create_adapter(engine=get_dialect("databricks"), spark=spark, ws=ws, secret_scope=secret_scope)

    return source, target


def _get_missing_data(
    reader: DataSource,
    sampler: SamplingQueryBuilder,
    missing_df: DataFrame,
    catalog: str,
    schema: str,
    table_name: str,
) -> DataFrame:
    sample_query = sampler.build_query(missing_df)
    return reader.read_data(
        catalog=catalog,
        schema=schema,
        table=table_name,
        query=sample_query,
        options=None,
    )


def reconcile_aggregates(
    ws: WorkspaceClient,
    spark: SparkSession,
    table_recon: TableRecon,
    reconcile_config: ReconcileConfig,
    local_test_run: bool = False,
):
    """[EXPERIMENTAL] Reconcile the aggregated data between the source and target tables.
    for e.g., COUNT, SUM, AVG of columns between source and target with or without any specific key/group by columns
    Supported Aggregate functions: MIN, MAX, COUNT, SUM, AVG, MEAN, MODE, PERCENTILE, STDDEV, VARIANCE, MEDIAN
    """
    # verify the workspace client and add proper product and version details
    # TODO For now we are utilising the
    #  verify_workspace_client from transpile/execute.py file. Later verify_workspace_client function has to be
    #  refactored

    ws_client: WorkspaceClient = verify_workspace_client(ws)

    report_type = ""
    if report_type:
        logger.info(f"report_type: {report_type}")
    logger.info(f"data_source: {reconcile_config.data_source}")

    # Read the reconcile_config and initialise the source and target data sources. Target is always Databricks
    source, target = initialise_data_source(
        engine=get_dialect(reconcile_config.data_source),
        spark=spark,
        ws=ws_client,
        secret_scope=reconcile_config.secret_scope,
    )

    # Generate Unique recon_id for every run
    recon_id = str(uuid4())

    # initialise the Reconciliation
    reconciler = Reconciliation(
        source,
        target,
        reconcile_config.database_config,
        report_type,
        SchemaCompare(spark=spark),
        get_dialect(reconcile_config.data_source),
        spark,
        metadata_config=reconcile_config.metadata_config,
    )

    # initialise the recon capture class
    recon_capture = ReconCapture(
        database_config=reconcile_config.database_config,
        recon_id=recon_id,
        report_type=report_type,
        source_dialect=get_dialect(reconcile_config.data_source),
        ws=ws_client,
        spark=spark,
        metadata_config=reconcile_config.metadata_config,
        local_test_run=local_test_run,
    )

    # Get the Aggregated Reconciliation Output for each table
    for table_conf in table_recon.tables:
        recon_process_duration = ReconcileProcessDuration(start_ts=str(datetime.now()), end_ts=None)
        try:
            src_schema, tgt_schema = _get_schema(
                source=source,
                target=target,
                table_conf=table_conf,
                database_config=reconcile_config.database_config,
            )
        except DataSourceRuntimeException as e:
            raise ReconciliationException(message=str(e)) from e

        assert table_conf.aggregates, "Aggregates must be defined for Aggregates Reconciliation"

        table_reconcile_agg_output_list: list[AggregateQueryOutput] = _run_reconcile_aggregates(
            reconciler=reconciler,
            table_conf=table_conf,
            src_schema=src_schema,
            tgt_schema=tgt_schema,
        )

        recon_process_duration.end_ts = str(datetime.now())

        # Persist the data to the delta tables
        recon_capture.store_aggregates_metrics(
            reconcile_agg_output_list=table_reconcile_agg_output_list,
            table_conf=table_conf,
            recon_process_duration=recon_process_duration,
        )

        (
            ReconIntermediatePersist(
                spark=spark,
                path=generate_volume_path(table_conf, reconcile_config.metadata_config),
            ).clean_unmatched_df_from_volume()
        )

    return _verify_successful_reconciliation(
        generate_final_reconcile_aggregate_output(
            recon_id=recon_id,
            spark=spark,
            metadata_config=reconcile_config.metadata_config,
            local_test_run=local_test_run,
        ),
        operation_name=AGG_RECONCILE_OPERATION_NAME,
    )


class Reconciliation:

    def __init__(
        self,
        source: DataSource,
        target: DataSource,
        database_config: DatabaseConfig,
        report_type: str,
        schema_comparator: SchemaCompare,
        source_engine: Dialect,
        spark: SparkSession,
        metadata_config: ReconcileMetadataConfig,
    ):
        self._source = source
        self._target = target
        self._report_type = report_type
        self._database_config = database_config
        self._schema_comparator = schema_comparator
        self._target_engine = get_dialect("databricks")
        self._source_engine = source_engine
        self._spark = spark
        self._metadata_config = metadata_config

    def reconcile_data(
        self,
        table_conf: Table,
        src_schema: list[Schema],
        tgt_schema: list[Schema],
    ) -> DataReconcileOutput:
        data_reconcile_output = self._get_reconcile_output(table_conf, src_schema, tgt_schema)
        reconcile_output = data_reconcile_output
        if self._report_type in {"data", "all"}:
            reconcile_output = self._get_sample_data(table_conf, data_reconcile_output, src_schema, tgt_schema)
            if table_conf.get_threshold_columns("source"):
                reconcile_output.threshold_output = self._reconcile_threshold_data(table_conf, src_schema, tgt_schema)

        if self._report_type == "row" and table_conf.get_threshold_columns("source"):
            logger.warning("Threshold comparison is ignored for 'row' report type")

        return reconcile_output

    def reconcile_schema(
        self,
        src_schema: list[Schema],
        tgt_schema: list[Schema],
        table_conf: Table,
    ):
        return self._schema_comparator.compare(src_schema, tgt_schema, self._source_engine, table_conf)

    def reconcile_aggregates(
        self,
        table_conf: Table,
        src_schema: list[Schema],
        tgt_schema: list[Schema],
    ) -> list[AggregateQueryOutput]:
        return self._get_reconcile_aggregate_output(table_conf, src_schema, tgt_schema)

    def _get_reconcile_output(
        self,
        table_conf,
        src_schema,
        tgt_schema,
    ):
        src_hash_query = HashQueryBuilder(table_conf, src_schema, "source", self._source_engine).build_query(
            report_type=self._report_type
        )
        tgt_hash_query = HashQueryBuilder(table_conf, tgt_schema, "target", self._source_engine).build_query(
            report_type=self._report_type
        )
        src_data = self._source.read_data(
            catalog=self._database_config.source_catalog,
            schema=self._database_config.source_schema,
            table=table_conf.source_name,
            query=src_hash_query,
            options=table_conf.jdbc_reader_options,
        )
        tgt_data = self._target.read_data(
            catalog=self._database_config.target_catalog,
            schema=self._database_config.target_schema,
            table=table_conf.target_name,
            query=tgt_hash_query,
            options=table_conf.jdbc_reader_options,
        )

        volume_path = generate_volume_path(table_conf, self._metadata_config)
        return reconcile_data(
            source=src_data,
            target=tgt_data,
            key_columns=table_conf.join_columns,
            report_type=self._report_type,
            spark=self._spark,
            path=volume_path,
        )

    def _get_reconcile_aggregate_output(
        self,
        table_conf,
        src_schema,
        tgt_schema,
    ):
        """
        Creates a single Query, for the aggregates having the same group by columns. (Ex: 1)
        If there are no group by columns, all the aggregates are clubbed together in a single query. (Ex: 2)
        Examples:
            1.  {
                      "type": "MIN",
                      "agg_cols": ["COL1"],
                      "group_by_cols": ["COL4"]
                    },
                    {
                      "type": "MAX",
                      "agg_cols": ["COL2"],
                      "group_by_cols": ["COL9"]
                    },
                    {
                      "type": "COUNT",
                      "agg_cols": ["COL2"],
                      "group_by_cols": ["COL9"]
                    },
                    {
                      "type": "AVG",
                      "agg_cols": ["COL3"],
                      "group_by_cols": ["COL4"]
                    },
              Query 1: SELECT MIN(COL1), AVG(COL3) FROM :table GROUP BY COL4
              Rules: ID  | Aggregate Type | Column | Group By Column
                         #1,   MIN,                      COL1,     COL4
                         #2,   AVG,                     COL3,      COL4
              -------------------------------------------------------
              Query 2: SELECT MAX(COL2), COUNT(COL2) FROM :table GROUP BY COL9
              Rules: ID  | Aggregate Type | Column | Group By Column
                         #1,   MAX,                      COL2,     COL9
                         #2,   COUNT,                COL2,      COL9
          2.  {
              "type": "MAX",
              "agg_cols": ["COL1"]
            },
            {
              "type": "SUM",
              "agg_cols": ["COL2"]
            },
            {
              "type": "MAX",
              "agg_cols": ["COL3"]
            }
          Query: SELECT MAX(COL1), SUM(COL2), MAX(COL3) FROM :table
          Rules: ID  | Aggregate Type | Column | Group By Column
                     #1, MAX, COL1,
                     #2, SUM, COL2,
                     #3, MAX, COL3,
        """

        src_query_builder = AggregateQueryBuilder(
            table_conf,
            src_schema,
            "source",
            self._source_engine,
        )

        # build Aggregate queries for source,
        src_agg_queries: list[AggregateQueryRules] = src_query_builder.build_queries()

        # There could be one or more queries per table based on the group by columns

        # build Aggregate queries for target(Databricks),
        tgt_agg_queries: list[AggregateQueryRules] = AggregateQueryBuilder(
            table_conf,
            tgt_schema,
            "target",
            self._target_engine,
        ).build_queries()

        volume_path = generate_volume_path(table_conf, self._metadata_config)

        table_agg_output: list[AggregateQueryOutput] = []

        # Iterate over the grouped aggregates and reconcile the data
        # Zip all the keys, read the source, target data for each Aggregate query
        # and reconcile on the aggregate data
        # For e.g., (source_query_GRP1, target_query_GRP1), (source_query_GRP2, target_query_GRP2)
        for src_query_with_rules, tgt_query_with_rules in zip(src_agg_queries, tgt_agg_queries):
            # For each Aggregate query, read the Source and Target Data and add a hash column

            rules_reconcile_output: list[AggregateQueryOutput] = []
            src_data = None
            tgt_data = None
            joined_df = None
            data_source_exception = None
            try:
                src_data = self._source.read_data(
                    catalog=self._database_config.source_catalog,
                    schema=self._database_config.source_schema,
                    table=table_conf.source_name,
                    query=src_query_with_rules.query,
                    options=table_conf.jdbc_reader_options,
                )
                tgt_data = self._target.read_data(
                    catalog=self._database_config.target_catalog,
                    schema=self._database_config.target_schema,
                    table=table_conf.target_name,
                    query=tgt_query_with_rules.query,
                    options=table_conf.jdbc_reader_options,
                )
                # Join the Source and Target Aggregated data
                joined_df = join_aggregate_data(
                    source=src_data,
                    target=tgt_data,
                    key_columns=src_query_with_rules.group_by_columns,
                    spark=self._spark,
                    path=f"{volume_path}{src_query_with_rules.group_by_columns_as_str}",
                )
            except DataSourceRuntimeException as e:
                data_source_exception = e

            # For each Aggregated Query, reconcile the data based on the rule
            for rule in src_query_with_rules.rules:
                if data_source_exception:
                    rule_reconcile_output = DataReconcileOutput(exception=str(data_source_exception))
                else:
                    rule_reconcile_output = reconcile_agg_data_per_rule(
                        joined_df, src_data.columns, tgt_data.columns, rule
                    )
                rules_reconcile_output.append(AggregateQueryOutput(rule=rule, reconcile_output=rule_reconcile_output))

            # For each table, there could be many Aggregated queries.
            # Collect the list of Rule Reconcile output per each Aggregate query and append it to the list
            table_agg_output.extend(rules_reconcile_output)
        return table_agg_output

    def _get_sample_data(
        self,
        table_conf,
        reconcile_output,
        src_schema,
        tgt_schema,
    ):
        mismatch = None
        missing_in_src = None
        missing_in_tgt = None

        if (
            reconcile_output.mismatch_count > 0
            or reconcile_output.missing_in_src_count > 0
            or reconcile_output.missing_in_tgt_count > 0
        ):
            src_sampler = SamplingQueryBuilder(table_conf, src_schema, "source", self._source_engine)
            tgt_sampler = SamplingQueryBuilder(table_conf, tgt_schema, "target", self._target_engine)
            if reconcile_output.mismatch_count > 0:
                mismatch = self._get_mismatch_data(
                    src_sampler,
                    tgt_sampler,
                    reconcile_output.mismatch_count,
                    reconcile_output.mismatch.mismatch_df,
                    table_conf.join_columns,
                    table_conf.source_name,
                    table_conf.target_name,
                    table_conf.sampling_options,
                )

            if reconcile_output.missing_in_src_count > 0:
                missing_in_src = _get_missing_data(
                    self._target,
                    tgt_sampler,
                    reconcile_output.missing_in_src,
                    self._database_config.target_catalog,
                    self._database_config.target_schema,
                    table_conf.target_name,
                )

            if reconcile_output.missing_in_tgt_count > 0:
                missing_in_tgt = _get_missing_data(
                    self._source,
                    src_sampler,
                    reconcile_output.missing_in_tgt,
                    self._database_config.source_catalog,
                    self._database_config.source_schema,
                    table_conf.source_name,
                )

        return DataReconcileOutput(
            mismatch=mismatch,
            mismatch_count=reconcile_output.mismatch_count,
            missing_in_src_count=reconcile_output.missing_in_src_count,
            missing_in_tgt_count=reconcile_output.missing_in_tgt_count,
            missing_in_src=missing_in_src,
            missing_in_tgt=missing_in_tgt,
        )

    def _get_mismatch_data(
        self,
        src_sampler,
        tgt_sampler,
        mismatch_count,
        mismatch,
        key_columns,
        src_table: str,
        tgt_table: str,
        sampling_options: SamplingOptions,
    ):

        tgt_sampling_query = tgt_sampler.build_query_with_alias()

        sampling_model_target = self._target.read_data(
            catalog=self._database_config.target_catalog,
            schema=self._database_config.target_schema,
            table=tgt_table,
            query=tgt_sampling_query,
            options=None,
        )

        # Uses pre-calculated `mismatch_count` from `reconcile_output.mismatch_count` to avoid from recomputing `mismatch` for RandomSampler.
        mismatch_sampler = SamplerFactory.get_sampler(sampling_options)
        df = mismatch_sampler.sample(mismatch, mismatch_count, key_columns, sampling_model_target).cache()

        src_mismatch_sample_query = src_sampler.build_query(df)
        tgt_mismatch_sample_query = tgt_sampler.build_query(df)

        src_data = self._source.read_data(
            catalog=self._database_config.source_catalog,
            schema=self._database_config.source_schema,
            table=src_table,
            query=src_mismatch_sample_query,
            options=None,
        )
        tgt_data = self._target.read_data(
            catalog=self._database_config.target_catalog,
            schema=self._database_config.target_schema,
            table=tgt_table,
            query=tgt_mismatch_sample_query,
            options=None,
        )

        return capture_mismatch_data_and_columns(source=src_data, target=tgt_data, key_columns=key_columns)

    def _reconcile_threshold_data(
        self,
        table_conf: Table,
        src_schema: list[Schema],
        tgt_schema: list[Schema],
    ):

        src_data, tgt_data = self._get_threshold_data(table_conf, src_schema, tgt_schema)

        source_view = f"source_{table_conf.source_name}_df_threshold_vw"
        target_view = f"target_{table_conf.target_name}_df_threshold_vw"

        src_data.createOrReplaceTempView(source_view)
        tgt_data.createOrReplaceTempView(target_view)

        return self._compute_threshold_comparison(table_conf, src_schema)

    def _get_threshold_data(
        self,
        table_conf: Table,
        src_schema: list[Schema],
        tgt_schema: list[Schema],
    ) -> tuple[DataFrame, DataFrame]:
        src_threshold_query = ThresholdQueryBuilder(
            table_conf, src_schema, "source", self._source_engine
        ).build_threshold_query()
        tgt_threshold_query = ThresholdQueryBuilder(
            table_conf, tgt_schema, "target", self._target_engine
        ).build_threshold_query()

        src_data = self._source.read_data(
            catalog=self._database_config.source_catalog,
            schema=self._database_config.source_schema,
            table=table_conf.source_name,
            query=src_threshold_query,
            options=table_conf.jdbc_reader_options,
        )
        tgt_data = self._target.read_data(
            catalog=self._database_config.target_catalog,
            schema=self._database_config.target_schema,
            table=table_conf.target_name,
            query=tgt_threshold_query,
            options=table_conf.jdbc_reader_options,
        )

        return src_data, tgt_data

    def _compute_threshold_comparison(self, table_conf: Table, src_schema: list[Schema]) -> ThresholdOutput:
        threshold_comparison_query = ThresholdQueryBuilder(
            table_conf, src_schema, "target", self._target_engine
        ).build_comparison_query()

        threshold_result = self._target.read_data(
            catalog=self._database_config.target_catalog,
            schema=self._database_config.target_schema,
            table=table_conf.target_name,
            query=threshold_comparison_query,
            options=table_conf.jdbc_reader_options,
        )
        threshold_columns = table_conf.get_threshold_columns("source")
        failed_where_cond = " OR ".join([name + "_match = 'Failed'" for name in threshold_columns])
        mismatched_df = threshold_result.filter(failed_where_cond)
        mismatched_count = mismatched_df.count()
        threshold_df = None
        if mismatched_count > 0:
            threshold_df = mismatched_df.limit(_SAMPLE_ROWS)

        return ThresholdOutput(threshold_df=threshold_df, threshold_mismatch_count=mismatched_count)

    def get_record_count(self, table_conf: Table, report_type: str) -> ReconcileRecordCount:
        if report_type != "schema":
            source_count_query = CountQueryBuilder(table_conf, "source", self._source_engine).build_query()
            target_count_query = CountQueryBuilder(table_conf, "target", self._target_engine).build_query()
            source_count_row = self._source.read_data(
                catalog=self._database_config.source_catalog,
                schema=self._database_config.source_schema,
                table=table_conf.source_name,
                query=source_count_query,
                options=None,
            ).first()
            target_count_row = self._target.read_data(
                catalog=self._database_config.target_catalog,
                schema=self._database_config.target_schema,
                table=table_conf.target_name,
                query=target_count_query,
                options=None,
            ).first()

            source_count = int(source_count_row[0]) if source_count_row is not None else 0
            target_count = int(target_count_row[0]) if target_count_row is not None else 0

            return ReconcileRecordCount(source=int(source_count), target=int(target_count))
        return ReconcileRecordCount()


def _get_schema(
    source: DataSource,
    target: DataSource,
    table_conf: Table,
    database_config: DatabaseConfig,
) -> tuple[list[Schema], list[Schema]]:
    src_schema = source.get_schema(
        catalog=database_config.source_catalog,
        schema=database_config.source_schema,
        table=table_conf.source_name,
    )
    tgt_schema = target.get_schema(
        catalog=database_config.target_catalog,
        schema=database_config.target_schema,
        table=table_conf.target_name,
    )

    return src_schema, tgt_schema


def _run_reconcile_data(
    reconciler: Reconciliation,
    table_conf: Table,
    src_schema: list[Schema],
    tgt_schema: list[Schema],
) -> DataReconcileOutput:
    try:
        return reconciler.reconcile_data(table_conf=table_conf, src_schema=src_schema, tgt_schema=tgt_schema)
    except DataSourceRuntimeException as e:
        return DataReconcileOutput(exception=str(e))


def _run_reconcile_schema(
    reconciler: Reconciliation,
    table_conf: Table,
    src_schema: list[Schema],
    tgt_schema: list[Schema],
):
    try:
        return reconciler.reconcile_schema(table_conf=table_conf, src_schema=src_schema, tgt_schema=tgt_schema)
    except PySparkException as e:
        return SchemaReconcileOutput(is_valid=False, exception=str(e))


def _run_reconcile_aggregates(
    reconciler: Reconciliation,
    table_conf: Table,
    src_schema: list[Schema],
    tgt_schema: list[Schema],
) -> list[AggregateQueryOutput]:
    try:
        return reconciler.reconcile_aggregates(table_conf, src_schema, tgt_schema)
    except DataSourceRuntimeException as e:
        return [AggregateQueryOutput(reconcile_output=DataReconcileOutput(exception=str(e)), rule=None)]


if __name__ == "__main__":
    if "DATABRICKS_RUNTIME_VERSION" not in os.environ:
        raise SystemExit("Only intended to run in Databricks Runtime")
    main(*sys.argv)
