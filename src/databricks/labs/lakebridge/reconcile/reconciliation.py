import logging

from pyspark.sql import DataFrame, SparkSession
from sqlglot import Dialect

from databricks.labs.lakebridge.config import (
    DatabaseConfig,
    ReconcileMetadataConfig,
)
from databricks.labs.lakebridge.reconcile import utils
from databricks.labs.lakebridge.reconcile.compare import (
    capture_mismatch_data_and_columns,
    reconcile_data,
    join_aggregate_data,
    reconcile_agg_data_per_rule,
)
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.exception import (
    DataSourceRuntimeException,
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
from databricks.labs.lakebridge.reconcile.recon_config import (
    Schema,
    Table,
    SamplingOptions,
)
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    DataReconcileOutput,
    ThresholdOutput,
    ReconcileRecordCount,
    AggregateQueryOutput,
)
from databricks.labs.lakebridge.reconcile.sampler import SamplerFactory
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

logger = logging.getLogger(__name__)
_SAMPLE_ROWS = 50


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

    @property
    def source(self) -> DataSource:
        return self._source

    @property
    def target(self) -> DataSource:
        return self._target

    @property
    def report_type(self) -> str:
        return self._report_type

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
        src_hash_query = HashQueryBuilder(
            table_conf, src_schema, "source", self._source_engine, self._source
        ).build_query(report_type=self._report_type)
        tgt_hash_query = HashQueryBuilder(
            table_conf, tgt_schema, "target", self._source_engine, self._target
        ).build_query(report_type=self._report_type)
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

        volume_path = utils.generate_volume_path(table_conf, self._metadata_config)
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

        # build Aggregate queries for source, There could be one
        # or more queries per table based on the group by columns
        src_agg_queries = AggregateQueryBuilder(
            table_conf,
            src_schema,
            "source",
            self._source_engine,
            self._source,
        ).build_queries()

        # build Aggregate queries for target(Databricks),
        tgt_agg_queries = AggregateQueryBuilder(
            table_conf,
            tgt_schema,
            "target",
            self._target_engine,
            self._target,
        ).build_queries()

        volume_path = utils.generate_volume_path(table_conf, self._metadata_config)

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
            src_sampler = SamplingQueryBuilder(table_conf, src_schema, "source", self._source_engine, self._source)
            tgt_sampler = SamplingQueryBuilder(table_conf, tgt_schema, "target", self._target_engine, self._target)
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
                missing_in_src = Reconciliation._get_missing_data(
                    self._target,
                    tgt_sampler,
                    reconcile_output.missing_in_src,
                    self._database_config.target_catalog,
                    self._database_config.target_schema,
                    table_conf.target_name,
                )

            if reconcile_output.missing_in_tgt_count > 0:
                missing_in_tgt = Reconciliation._get_missing_data(
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
            table_conf, src_schema, "source", self._source_engine, self._source
        ).build_threshold_query()
        tgt_threshold_query = ThresholdQueryBuilder(
            table_conf, tgt_schema, "target", self._target_engine, self._target
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
            table_conf, src_schema, "target", self._target_engine, self._target
        ).build_comparison_query()

        threshold_result = self._target.read_data(
            catalog=self._database_config.target_catalog,
            schema=self._database_config.target_schema,
            table=table_conf.target_name,
            query=threshold_comparison_query,
            options=table_conf.jdbc_reader_options,
        )
        threshold_columns = table_conf.get_threshold_columns("source")
        failed_where_cond = " OR ".join(
            ["`" + DialectUtils.unnormalize_identifier(name) + "_match` = 'Failed'" for name in threshold_columns]
        )
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

    @staticmethod
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
