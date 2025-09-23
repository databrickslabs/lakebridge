import logging
from datetime import datetime
from functools import reduce

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col, collect_list, create_map, lit
from pyspark.sql.types import StringType, StructField, StructType
from pyspark.errors import PySparkException
from sqlglot import Dialect

from databricks.labs.lakebridge.config import DatabaseConfig, Table, ReconcileMetadataConfig
from databricks.labs.lakebridge.reconcile.recon_config import TableThresholds
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_key_from_dialect
from databricks.labs.lakebridge.reconcile.exception import (
    WriteToTableException,
    ReadAndWriteWithVolumeException,
    CleanFromVolumeException,
)
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    DataReconcileOutput,
    ReconcileOutput,
    ReconcileProcessDuration,
    ReconcileTableOutput,
    SchemaReconcileOutput,
    StatusOutput,
    ReconcileRecordCount,
    AggregateQueryOutput,
)
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)

_RECON_TABLE_NAME = "main"
_RECON_METRICS_TABLE_NAME = "metrics"
_RECON_DETAILS_TABLE_NAME = "details"
_RECON_AGGREGATE_RULES_TABLE_NAME = "aggregate_rules"
_RECON_AGGREGATE_METRICS_TABLE_NAME = "aggregate_metrics"
_RECON_AGGREGATE_DETAILS_TABLE_NAME = "aggregate_details"


class ReconIntermediatePersist:

    def __init__(self, spark: SparkSession, path: str):
        self.spark = spark
        self.path = path

    def _write_unmatched_df_to_volumes(
        self,
        unmatched_df: DataFrame,
    ) -> None:
        unmatched_df.write.format("parquet").mode("overwrite").save(self.path)

    def _read_unmatched_df_from_volumes(self) -> DataFrame:
        return self.spark.read.format("parquet").load(self.path)

    def clean_unmatched_df_from_volume(self):
        try:
            # TODO: for now we are overwriting the intermediate cache path. We should delete the volume in future
            # workspace_client.dbfs.get_status(path)
            # workspace_client.dbfs.delete(path, recursive=True)
            empty_df = self.spark.createDataFrame([], schema=StructType([StructField("empty", StringType(), True)]))
            empty_df.write.format("parquet").mode("overwrite").save(self.path)
            logger.warning(f"Unmatched DF cleaned up from {self.path} successfully.")
        except PySparkException as e:
            message = f"Error cleaning up unmatched DF from {self.path} volumes --> {e}"
            logger.error(message)
            raise CleanFromVolumeException(message) from e

    def write_and_read_unmatched_df_with_volumes(
        self,
        unmatched_df: DataFrame,
    ) -> DataFrame:
        try:
            self._write_unmatched_df_to_volumes(unmatched_df)
            return self._read_unmatched_df_from_volumes()
        except PySparkException as e:
            message = f"Exception in reading or writing unmatched DF with volumes {self.path} --> {e}"
            logger.error(message)
            raise ReadAndWriteWithVolumeException(message) from e


def _write_df_to_delta(df: DataFrame, table_name: str, mode="append"):
    try:
        df.write.mode(mode).saveAsTable(table_name)
        logger.info(f"Data written to {table_name} successfully.")
    except Exception as e:
        message = f"Error writing data to {table_name}: {e}"
        logger.error(message)
        raise WriteToTableException(message) from e


def generate_final_reconcile_output(
    recon_id: str,
    spark: SparkSession,
    metadata_config: ReconcileMetadataConfig = ReconcileMetadataConfig(),
    local_test_run: bool = False,
) -> ReconcileOutput:
    _db_prefix = "default" if local_test_run else f"{metadata_config.catalog}.{metadata_config.schema}"
    recon_df = spark.sql(
        f"""
    SELECT
    CASE
        WHEN COALESCE(MAIN.SOURCE_TABLE.CATALOG, '') <> '' THEN CONCAT(MAIN.SOURCE_TABLE.CATALOG, '.', MAIN.SOURCE_TABLE.SCHEMA, '.', MAIN.SOURCE_TABLE.TABLE_NAME)
        ELSE CONCAT(MAIN.SOURCE_TABLE.SCHEMA, '.', MAIN.SOURCE_TABLE.TABLE_NAME)
    END AS SOURCE_TABLE,
    CONCAT(MAIN.TARGET_TABLE.CATALOG, '.', MAIN.TARGET_TABLE.SCHEMA, '.', MAIN.TARGET_TABLE.TABLE_NAME) AS TARGET_TABLE,
    CASE WHEN lower(MAIN.report_type) in ('all', 'row', 'data') THEN
    CASE
        WHEN METRICS.recon_metrics.row_comparison.missing_in_source = 0 AND METRICS.recon_metrics.row_comparison.missing_in_target = 0 THEN TRUE
        ELSE FALSE
    END
    ELSE NULL END AS ROW,
    CASE WHEN lower(MAIN.report_type) in ('all', 'data') THEN
    CASE
        WHEN (METRICS.run_metrics.status = true) or
         (METRICS.recon_metrics.column_comparison.absolute_mismatch = 0 AND METRICS.recon_metrics.column_comparison.threshold_mismatch = 0 AND METRICS.recon_metrics.column_comparison.mismatch_columns = '') THEN TRUE
        ELSE FALSE
    END
    ELSE NULL END AS COLUMN,
    CASE WHEN lower(MAIN.report_type) in ('all', 'schema') THEN
    CASE
        WHEN METRICS.recon_metrics.schema_comparison = true THEN TRUE
        ELSE FALSE
    END
    ELSE NULL END AS SCHEMA,
    METRICS.run_metrics.exception_message AS EXCEPTION_MESSAGE
    FROM
        {_db_prefix}.{_RECON_TABLE_NAME} MAIN
    INNER JOIN
        {_db_prefix}.{_RECON_METRICS_TABLE_NAME} METRICS
    ON
        (MAIN.recon_table_id = METRICS.recon_table_id)
    WHERE
        MAIN.recon_id = '{recon_id}'
    """
    )
    table_output = []
    for row in recon_df.collect():
        if row.EXCEPTION_MESSAGE is not None and row.EXCEPTION_MESSAGE != "":
            table_output.append(
                ReconcileTableOutput(
                    target_table_name=row.TARGET_TABLE,
                    source_table_name=row.SOURCE_TABLE,
                    status=StatusOutput(),
                    exception_message=row.EXCEPTION_MESSAGE,
                )
            )
        else:
            table_output.append(
                ReconcileTableOutput(
                    target_table_name=row.TARGET_TABLE,
                    source_table_name=row.SOURCE_TABLE,
                    status=StatusOutput(row=row.ROW, column=row.COLUMN, schema=row.SCHEMA),
                    exception_message=row.EXCEPTION_MESSAGE,
                )
            )
    final_reconcile_output = ReconcileOutput(recon_id=recon_id, results=table_output)
    logger.info(f"Final reconcile output: {final_reconcile_output}")
    return final_reconcile_output


def generate_final_reconcile_aggregate_output(
    recon_id: str,
    spark: SparkSession,
    metadata_config: ReconcileMetadataConfig = ReconcileMetadataConfig(),
    local_test_run: bool = False,
) -> ReconcileOutput:
    _db_prefix = "default" if local_test_run else f"{metadata_config.catalog}.{metadata_config.schema}"
    recon_df = spark.sql(
        f"""
        SELECT source_table,
         target_table,
          EVERY(status) AS status,
           ARRAY_JOIN(COLLECT_SET(exception_message), '\n') AS exception_message
        FROM
        (SELECT
            IF(ISNULL(main.source_table.catalog)
                , CONCAT_WS('.', main.source_table.schema, main.source_table.table_name)
                , CONCAT_WS('.', main.source_table.catalog, main.source_table.schema, main.source_table.table_name)) AS source_table,
            CONCAT_WS('.', main.target_table.catalog, main.target_table.schema, main.target_table.table_name) AS target_table,
            IF(metrics.run_metrics.status='true', TRUE , FALSE) AS status,
            metrics.run_metrics.exception_message AS exception_message
            FROM
                {_db_prefix}.{_RECON_TABLE_NAME} main
            INNER JOIN
                {_db_prefix}.{_RECON_AGGREGATE_METRICS_TABLE_NAME} metrics
            ON
                (MAIN.recon_table_id = METRICS.recon_table_id
                AND MAIN.operation_name = 'aggregates-reconcile')
            WHERE
                MAIN.recon_id = '{recon_id}'
        )
        GROUP BY source_table, target_table;
    """
    )
    table_output = []
    for row in recon_df.collect():
        if row.exception_message is not None and row.exception_message != "":
            table_output.append(
                ReconcileTableOutput(
                    target_table_name=row.target_table,
                    source_table_name=row.source_table,
                    status=StatusOutput(),
                    exception_message=row.exception_message,
                )
            )
        else:
            table_output.append(
                ReconcileTableOutput(
                    target_table_name=row.target_table,
                    source_table_name=row.source_table,
                    status=StatusOutput(aggregate=row.status),
                    exception_message=row.exception_message,
                )
            )
    final_reconcile_output = ReconcileOutput(recon_id=recon_id, results=table_output)
    logger.info(f"Final reconcile output: {final_reconcile_output}")
    return final_reconcile_output


class ReconCapture:

    def __init__(
        self,
        database_config: DatabaseConfig,
        recon_id: str,
        report_type: str,
        source_dialect: Dialect,
        ws: WorkspaceClient,
        spark: SparkSession,
        metadata_config: ReconcileMetadataConfig = ReconcileMetadataConfig(),
        local_test_run: bool = False,
    ):
        self.database_config = database_config
        self.recon_id = recon_id
        self.report_type = report_type
        self.source_dialect = source_dialect
        self.ws = ws
        self.spark = spark
        self._db_prefix = "default" if local_test_run else f"{metadata_config.catalog}.{metadata_config.schema}"

    def _generate_recon_main_id(
        self,
        table_conf: Table,
    ) -> int:
        full_source_table = (
            f"{self.database_config.source_schema}.{table_conf.source_name}"
            if self.database_config.source_catalog is None
            else f"{self.database_config.source_catalog}.{self.database_config.source_schema}.{table_conf.source_name}"
        )
        full_target_table = (
            f"{self.database_config.target_catalog}.{self.database_config.target_schema}.{table_conf.target_name}"
        )
        return hash(f"{self.recon_id}{full_source_table}{full_target_table}")

    def _insert_into_main_table(
        self,
        recon_table_id: int,
        table_conf: Table,
        recon_process_duration: ReconcileProcessDuration,
        operation_name: str = "reconcile",
    ) -> None:
        source_dialect_key = get_key_from_dialect(self.source_dialect)
        df = self.spark.sql(
            f"""
                select {recon_table_id} as recon_table_id,
                '{self.recon_id}' as recon_id,
                case
                    when '{source_dialect_key}' = 'databricks' then 'Databricks'
                    when '{source_dialect_key}' = 'snowflake' then 'Snowflake'
                    when '{source_dialect_key}' = 'oracle' then 'Oracle'
                    else '{source_dialect_key}'
                end as source_type,
                named_struct(
                    'catalog', case when '{self.database_config.source_catalog}' = 'None' then null else '{self.database_config.source_catalog}' end,
                    'schema', '{self.database_config.source_schema}',
                    'table_name', '{table_conf.source_name}'
                ) as source_table,
                named_struct(
                    'catalog', '{self.database_config.target_catalog}',
                    'schema', '{self.database_config.target_schema}',
                    'table_name', '{table_conf.target_name}'
                ) as target_table,
                '{self.report_type}' as report_type,
                '{operation_name}' as operation_name,
                cast('{recon_process_duration.start_ts}' as timestamp) as start_ts,
                cast('{recon_process_duration.end_ts}' as timestamp) as end_ts
            """
        )
        _write_df_to_delta(df, f"{self._db_prefix}.{_RECON_TABLE_NAME}")

    @classmethod
    def _is_mismatch_within_threshold_limits(
        cls, data_reconcile_output: DataReconcileOutput, table_conf: Table, record_count: ReconcileRecordCount
    ):
        total_mismatch_count = (
            data_reconcile_output.mismatch_count + data_reconcile_output.threshold_output.threshold_mismatch_count
        )
        logger.info(f"total_mismatch_count : {total_mismatch_count}")
        logger.warning(f"reconciled_record_count : {record_count}")
        # if the mismatch count is 0 then no need of checking bounds.
        if total_mismatch_count == 0:
            return True
        # pull out table thresholds
        thresholds: list[TableThresholds] = (
            [threshold for threshold in table_conf.table_thresholds if threshold.model == "mismatch"]
            if table_conf.table_thresholds
            else []
        )
        # if not table thresholds are provided return false
        if not thresholds:
            return False

        res = None
        for threshold in thresholds:
            mode = threshold.get_mode()
            lower_bound = int(threshold.lower_bound.replace("%", ""))
            upper_bound = int(threshold.upper_bound.replace("%", ""))
            if mode == "absolute":
                res = lower_bound <= total_mismatch_count <= upper_bound
            if mode == "percentage":
                lower_bound = int(round((lower_bound / 100) * record_count.source))
                upper_bound = int(round((upper_bound / 100) * record_count.source))
                res = lower_bound <= total_mismatch_count <= upper_bound

        return res

    def _insert_into_metrics_table(
        self,
        recon_table_id: int,
        data_reconcile_output: DataReconcileOutput,
        schema_reconcile_output: SchemaReconcileOutput,
        table_conf: Table,
        record_count: ReconcileRecordCount,
    ) -> None:
        status = False
        if data_reconcile_output.exception in {None, ''} and schema_reconcile_output.exception in {None, ''}:
            status = (
                # validate for both exact mismatch and threshold mismatch
                self._is_mismatch_within_threshold_limits(
                    data_reconcile_output=data_reconcile_output, table_conf=table_conf, record_count=record_count
                )
                and data_reconcile_output.missing_in_src_count == 0
                and data_reconcile_output.missing_in_tgt_count == 0
                and schema_reconcile_output.is_valid
            )

        exception_msg = ""
        if schema_reconcile_output.exception is not None:
            exception_msg = schema_reconcile_output.exception.replace("'", '').replace('"', '')
        if data_reconcile_output.exception is not None:
            exception_msg = data_reconcile_output.exception.replace("'", '').replace('"', '')

        insertion_time = str(datetime.now())
        mismatch_columns = []
        if data_reconcile_output.mismatch and data_reconcile_output.mismatch.mismatch_columns:
            mismatch_columns = data_reconcile_output.mismatch.mismatch_columns

        df = self.spark.sql(
            f"""
                select {recon_table_id} as recon_table_id,
                named_struct(
                    'row_comparison', case when '{self.report_type.lower()}' in ('all', 'row', 'data')
                        and '{exception_msg}' = '' then
                     named_struct(
                        'missing_in_source', cast({data_reconcile_output.missing_in_src_count} as bigint),
                        'missing_in_target', cast({data_reconcile_output.missing_in_tgt_count} as bigint)
                    ) else null end,
                    'column_comparison', case when '{self.report_type.lower()}' in ('all', 'data')
                        and '{exception_msg}' = '' then
                    named_struct(
                        'absolute_mismatch', cast({data_reconcile_output.mismatch_count} as bigint),
                        'threshold_mismatch', cast({data_reconcile_output.threshold_output.threshold_mismatch_count} as bigint),
                        'mismatch_columns', '{",".join(mismatch_columns)}'
                    ) else null end,
                    'schema_comparison', case when '{self.report_type.lower()}' in ('all', 'schema')
                        and '{exception_msg}' = '' then
                        {schema_reconcile_output.is_valid} else null end
                ) as recon_metrics,
                named_struct(
                    'status', {status},
                    'run_by_user', '{self.ws.current_user.me().user_name}',
                    'exception_message', "{exception_msg}"
                ) as run_metrics,
                cast('{insertion_time}' as timestamp) as inserted_ts
            """
        )
        _write_df_to_delta(df, f"{self._db_prefix}.{_RECON_METRICS_TABLE_NAME}")

    @classmethod
    def _create_map_column(
        cls,
        recon_table_id: int,
        df: DataFrame,
        recon_type: str,
        status: bool,
    ) -> DataFrame:
        columns = df.columns
        # Create a list of column names and their corresponding column values
        map_args = []
        for column in columns:
            map_args.extend([lit(column).alias(column + "_key"), col(column).cast("string").alias(column + "_value")])
        # Create a new DataFrame with a map column
        df = df.select(create_map(*map_args).alias("data"))
        df = (
            df.withColumn("recon_table_id", lit(recon_table_id))
            .withColumn("recon_type", lit(recon_type))
            .withColumn("status", lit(status))
            .withColumn("inserted_ts", lit(datetime.now()))
        )
        return (
            df.groupBy("recon_table_id", "recon_type", "status", "inserted_ts")
            .agg(collect_list("data").alias("data"))
            .selectExpr("recon_table_id", "recon_type", "status", "data", "inserted_ts")
        )

    def _create_map_column_and_insert(
        self,
        recon_table_id: int,
        df: DataFrame,
        recon_type: str,
        status: bool,
    ) -> None:
        df = self._create_map_column(recon_table_id, df, recon_type, status)
        _write_df_to_delta(df, f"{self._db_prefix}.{_RECON_DETAILS_TABLE_NAME}")

    def _insert_into_details_table(
        self,
        recon_table_id: int,
        reconcile_output: DataReconcileOutput,
        schema_output: SchemaReconcileOutput,
    ):
        if reconcile_output.mismatch_count > 0 and reconcile_output.mismatch.mismatch_df:
            self._create_map_column_and_insert(
                recon_table_id,
                reconcile_output.mismatch.mismatch_df,
                "mismatch",
                False,
            )

        if reconcile_output.missing_in_src_count > 0 and reconcile_output.missing_in_src:
            self._create_map_column_and_insert(
                recon_table_id,
                reconcile_output.missing_in_src,
                "missing_in_source",
                False,
            )

        if reconcile_output.missing_in_tgt_count > 0 and reconcile_output.missing_in_tgt:
            self._create_map_column_and_insert(
                recon_table_id,
                reconcile_output.missing_in_tgt,
                "missing_in_target",
                False,
            )

        if (
            reconcile_output.threshold_output.threshold_mismatch_count > 0
            and reconcile_output.threshold_output.threshold_df
        ):
            self._create_map_column_and_insert(
                recon_table_id,
                reconcile_output.threshold_output.threshold_df,
                "threshold_mismatch",
                False,
            )

        if schema_output.compare_df is not None:
            self._create_map_column_and_insert(
                recon_table_id, schema_output.compare_df, "schema", schema_output.is_valid
            )

    def _get_df(
        self,
        recon_table_id: int,
        agg_data: DataReconcileOutput,
        recon_type: str,
    ):

        column_count = agg_data.mismatch_count
        agg_df = agg_data.mismatch.mismatch_df
        match recon_type:
            case "missing_in_source":
                column_count = agg_data.missing_in_src_count
                agg_df = agg_data.missing_in_src
            case "missing_in_target":
                column_count = agg_data.missing_in_tgt_count
                agg_df = agg_data.missing_in_tgt

        if column_count > 0 and agg_df:
            return self._create_map_column(
                recon_table_id,
                agg_df,
                recon_type,
                False,
            )
        return None

    @classmethod
    def _union_dataframes(cls, df_list: list[DataFrame]) -> DataFrame:
        return reduce(lambda agg_df, df: agg_df.unionByName(df), df_list)

    def _insert_aggregates_into_metrics_table(
        self,
        recon_table_id: int,
        reconcile_agg_output_list: list[AggregateQueryOutput],
    ) -> None:

        agg_metrics_df_list = []
        for agg_output in reconcile_agg_output_list:
            agg_data = agg_output.reconcile_output

            status = False
            if agg_data.exception in {None, ''}:
                status = not (
                    agg_data.mismatch_count > 0
                    or agg_data.missing_in_src_count > 0
                    or agg_data.missing_in_tgt_count > 0
                )

            exception_msg = ""
            if agg_data.exception is not None:
                exception_msg = agg_data.exception.replace("'", '').replace('"', '')

            insertion_time = str(datetime.now())

            # If there is any exception while running the Query,
            # each rule is stored, with the Exception message in the metrics table
            assert agg_output.rule, "Aggregate Rule must be present for storing the metrics"
            rule_id = hash(f"{recon_table_id}_{agg_output.rule.column_from_rule}")

            agg_metrics_df = self.spark.sql(
                f"""
                    select {recon_table_id} as recon_table_id,
                    {rule_id}  as rule_id,
                    if('{exception_msg}' = '', named_struct(
                            'missing_in_source', {agg_data.missing_in_src_count},
                            'missing_in_target', {agg_data.missing_in_tgt_count},
                            'mismatch', {agg_data.mismatch_count}
                    ), null) as recon_metrics,
                    named_struct(
                        'status', {status},
                        'run_by_user', '{self.ws.current_user.me().user_name}',
                        'exception_message', "{exception_msg}"
                    ) as run_metrics,
                    cast('{insertion_time}' as timestamp) as inserted_ts
                """
            )
            agg_metrics_df_list.append(agg_metrics_df)

        agg_metrics_table_df = self._union_dataframes(agg_metrics_df_list)
        _write_df_to_delta(agg_metrics_table_df, f"{self._db_prefix}.{_RECON_AGGREGATE_METRICS_TABLE_NAME}")

    def _insert_aggregates_into_details_table(
        self, recon_table_id: int, reconcile_agg_output_list: list[AggregateQueryOutput]
    ):
        agg_details_df_list = []
        for agg_output in reconcile_agg_output_list:
            agg_details_rule_df_list = []

            mismatch_df = self._get_df(recon_table_id, agg_output.reconcile_output, "mismatch")
            if mismatch_df and not mismatch_df.isEmpty():
                agg_details_rule_df_list.append(mismatch_df)

            missing_src_df = self._get_df(recon_table_id, agg_output.reconcile_output, "missing_in_source")
            if missing_src_df and not missing_src_df.isEmpty():
                agg_details_rule_df_list.append(missing_src_df)

            missing_tgt_df = self._get_df(recon_table_id, agg_output.reconcile_output, "missing_in_target")
            if missing_tgt_df and not missing_tgt_df.isEmpty():
                agg_details_rule_df_list.append(missing_tgt_df)

            if agg_details_rule_df_list:
                agg_details_rule_df = self._union_dataframes(agg_details_rule_df_list)
                if agg_output.rule:
                    rule_id = hash(f"{recon_table_id}_{agg_output.rule.column_from_rule}")
                    agg_details_rule_df = agg_details_rule_df.withColumn("rule_id", lit(rule_id)).select(
                        "recon_table_id", "rule_id", "recon_type", "data", "inserted_ts"
                    )
                    agg_details_df_list.append(agg_details_rule_df)
            else:
                logger.info(
                    f"Aggregate rule reconciliation is successful."
                    f" No details to store."
                    f" Rule: {agg_output.rule.column_from_rule}"
                    if agg_output.rule
                    else ""
                )

        if agg_details_df_list:
            agg_details_table_df = self._union_dataframes(agg_details_df_list)
            _write_df_to_delta(agg_details_table_df, f"{self._db_prefix}.{_RECON_AGGREGATE_DETAILS_TABLE_NAME}")

    def start(
        self,
        data_reconcile_output: DataReconcileOutput,
        schema_reconcile_output: SchemaReconcileOutput,
        table_conf: Table,
        recon_process_duration: ReconcileProcessDuration,
        record_count: ReconcileRecordCount,
    ) -> None:
        recon_table_id = self._generate_recon_main_id(table_conf)
        self._insert_into_main_table(recon_table_id, table_conf, recon_process_duration)
        self._insert_into_metrics_table(
            recon_table_id, data_reconcile_output, schema_reconcile_output, table_conf, record_count
        )
        self._insert_into_details_table(recon_table_id, data_reconcile_output, schema_reconcile_output)

    def store_aggregates_metrics(
        self,
        table_conf: Table,
        recon_process_duration: ReconcileProcessDuration,
        reconcile_agg_output_list: list[AggregateQueryOutput],
    ) -> None:
        recon_table_id = self._generate_recon_main_id(table_conf)
        self._insert_into_main_table(recon_table_id, table_conf, recon_process_duration, 'aggregates-reconcile')
        self._insert_into_rules_table(recon_table_id, reconcile_agg_output_list)
        self._insert_aggregates_into_metrics_table(recon_table_id, reconcile_agg_output_list)
        self._insert_aggregates_into_details_table(
            recon_table_id,
            reconcile_agg_output_list,
        )

    def _insert_into_rules_table(self, recon_table_id: int, reconcile_agg_output_list: list[AggregateQueryOutput]):

        rule_df_list = []
        for agg_output in reconcile_agg_output_list:
            if not agg_output.rule:
                logger.error("Aggregate Rule must be present for storing the rules")
                continue
            rule_id = hash(f"{recon_table_id}_{agg_output.rule.column_from_rule}")
            rule_query = agg_output.rule.get_rule_query(rule_id)
            rule_df_list.append(
                self.spark.sql(rule_query)
                .withColumn("inserted_ts", lit(datetime.now()))
                .select("rule_id", "rule_type", "rule_info", "inserted_ts")
            )

        if rule_df_list:
            rules_table_df = self._union_dataframes(rule_df_list)
            _write_df_to_delta(rules_table_df, f"{self._db_prefix}.{_RECON_AGGREGATE_RULES_TABLE_NAME}")
