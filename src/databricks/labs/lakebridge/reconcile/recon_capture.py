import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from functools import reduce, cached_property
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col, collect_list, create_map, lit
from pyspark.errors import PySparkException
from sqlglot import Dialect

from databricks.labs.lakebridge.config import (
    SourceConnectionConfig,
    TargetConnectionConfig,
    Table,
    ReconcileMetadataConfig,
)
from databricks.labs.lakebridge.reconcile.fingerprint.metadata import FingerprintRunMetadata
from databricks.labs.lakebridge.reconcile.recon_config import TableThresholds
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_key_from_dialect
from databricks.labs.lakebridge.reconcile.exception import (
    WriteToTableException,
    ReadAndWriteWithVolumeException,
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


# Single source of truth for the persisted ``fingerprint_metrics`` named_struct.
# Tuple of (sql_field_name, dataclass_attribute, sql_type).
#
# Field ORDER must match ``FingerprintRunMetadata`` declaration order — Delta
# resolves struct fields positionally on saveAsTable, so reordering here would
# silently corrupt every recon_metrics row written against existing customer
# tables. The unit suite guards order.
#
# Allowed sql_type values:
#   - "bool"             -> ``true``/``false`` literal
#   - "bigint"           -> ``cast(N as bigint)`` literal
#   - "bigint_or_null"   -> ``cast(N as bigint)`` or SQL ``NULL``
#   - "string_or_null"   -> ``'value'`` (quote-scrubbed) or SQL ``NULL``
FP_METRICS_STRUCT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("eligible", "eligible", "bool"),
    ("ineligibility_reason", "ineligibility_reason", "string_or_null"),
    ("verdict", "verdict", "string_or_null"),
    ("elapsed_ms", "elapsed_ms", "bigint"),
    ("solved_count", "solved_count", "bigint"),
    ("unsolved_sb_count", "unsolved_sb_count", "bigint"),
    ("total_mismatched_sbs", "total_mismatched_sbs", "bigint"),
    ("fallback_to_full_pipeline", "fallback_to_full_pipeline", "bool"),
    ("sub_bucket_count", "sub_bucket_count", "bigint"),
    ("bucket_count", "bucket_count", "bigint"),
    ("target_row_count", "target_row_count", "bigint_or_null"),
    ("row_count_source", "row_count_source", "string_or_null"),
    ("fetch_path", "fetch_path", "string_or_null"),
)


def render_fp_metrics_value(value: object, sql_type: str) -> str:
    """Render a Python value to its SQL-literal form per the declared sql_type.

    Centralised so values cannot reach the persisted SQL fragment without
    flowing through type-aware rendering. An unknown ``sql_type`` raises
    rather than silently falling through to ``str(value)`` — adding a new
    field type is a deliberate change in this function, not an accident in
    a caller.
    """
    if sql_type == "bool":
        return str(bool(value)).lower()
    if sql_type == "bigint":
        # Dataclass typing pins this to ``int``; assertion is a true invariant
        # and also narrows ``value`` from ``object`` for mypy.
        assert isinstance(value, int), f"bigint field expected int, got {type(value).__name__}"
        return f"cast({value} as bigint)"
    if sql_type == "bigint_or_null":
        if value is None:
            # Typed NULL: a bare ``NULL`` literal makes Spark infer ``NullType`` for
            # the struct field, which then cannot be written/read by the vectorized
            # Parquet reader and breaks schema equality against the typed audit table.
            return "cast(NULL as bigint)"
        assert isinstance(value, int), f"bigint_or_null field expected int|None, got {type(value).__name__}"
        return f"cast({value} as bigint)"
    if sql_type == "string_or_null":
        if value is None:
            # Typed NULL — see ``bigint_or_null`` above.
            return "cast(NULL as string)"
        # Defense-in-depth: scrub embedded single/double quotes that would
        # terminate the SQL literal. Metadata values come from controlled
        # paths today; this guards a future field that carries user input.
        scrubbed = str(value).replace("'", "").replace('"', "")
        return f"'{scrubbed}'"
    raise ValueError(
        f"Unsupported sql_type for fingerprint_metrics struct: {sql_type!r}. "
        "Allowed: 'bool', 'bigint', 'bigint_or_null', 'string_or_null'."
    )


class AbstractReconIntermediatePersist:
    @property
    def base_dir(self) -> Path:
        raise NotImplementedError

    @property
    def is_serverless(self) -> bool:
        raise NotImplementedError

    def write_and_read_df_with_volumes(
        self,
        df: DataFrame,
    ) -> DataFrame:
        raise NotImplementedError


class ReconIntermediatePersist(AbstractReconIntermediatePersist):
    def __init__(self, spark: SparkSession, metadata_config: ReconcileMetadataConfig):
        self._spark = spark
        self._metadata_config = metadata_config
        self._format = "delta" if self._is_databricks else "parquet"
        self._base_dir = self._get_uc_volume_path if self._is_databricks else tempfile.gettempdir()

    @cached_property
    def _is_databricks(self) -> bool:
        is_db = "DATABRICKS_RUNTIME_VERSION" in os.environ
        logger.debug(f"Running on Databricks check completed with result: {is_db}")
        return is_db

    @property
    def base_dir(self) -> Path:
        return Path(self._base_dir)

    @cached_property
    def is_serverless(self) -> bool:
        is_serverless = (
            os.getenv("IS_SERVERLESS", "").lower() == "true"
            or os.getenv("DATABRICKS_SERVERLESS_COMPUTE_ID", "").lower() == "auto"
        )
        return is_serverless

    @property
    def _get_uc_volume_path(self):
        return (
            f"/Volumes/"
            f"{self._metadata_config.catalog}/"
            f"{self._metadata_config.schema}/"
            f"{self._metadata_config.volume}"
        )

    def _write_df_to_volumes(self, df: DataFrame, path: str) -> None:
        logger.debug(f"Writing DF on {self._format} to path: {path}")
        df.write.format(self._format).save(path)
        logger.info(f"Wrote DF on {self._format}")

    def _read_df_from_volumes(self, path) -> DataFrame:
        logger.debug(f"Reading DF on {self._format} from path: {path}")
        df = self._spark.read.format(self._format).load(path)
        logger.info(f"Read DF on {self._format}")
        return df

    def write_and_read_df_with_volumes(
        self,
        df: DataFrame,
    ) -> DataFrame:
        path = str(self.base_dir / uuid.uuid4().hex)
        try:
            self._write_df_to_volumes(df, path)
            return self._read_df_from_volumes(path)
        except PySparkException as e:
            message = f"Exception in reading or writing DF at: {path}"
            logger.exception(message)
            raise ReadAndWriteWithVolumeException(message) from e


def _write_df_to_delta(df: DataFrame, table_name: str, mode="append", *, merge_schema: bool = False):
    """Append to a Delta table; ``merge_schema=True`` enables additive column evolution.

    The fingerprint precheck adds a ``fingerprint_metrics`` struct to the
    ``recon_metrics`` row; on the first write against an existing customer
    table this column has to materialise without an explicit ``ALTER TABLE``,
    so callers writing that table pass ``merge_schema=True``.
    """
    try:
        writer = df.write.mode(mode)
        if merge_schema:
            writer = writer.option("mergeSchema", "true")
        writer.saveAsTable(table_name)
        logger.info(f"Data written to {table_name} successfully.")
    except Exception as e:
        message = f"Error writing data to {table_name}: {e}"
        logger.error(message)
        raise WriteToTableException(message) from e


def generate_final_reconcile_output(
    recon_id: str,
    spark: SparkSession,
    metadata_config: ReconcileMetadataConfig = ReconcileMetadataConfig(),
) -> ReconcileOutput:
    _db_prefix = f"{metadata_config.catalog}.{metadata_config.schema}"
    recon_df = spark.sql(f"""
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
    """)
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
) -> ReconcileOutput:
    _db_prefix = f"{metadata_config.catalog}.{metadata_config.schema}"
    recon_df = spark.sql(f"""
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
    """)
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
        source_connection: SourceConnectionConfig,
        target_connection: TargetConnectionConfig,
        recon_id: str,
        report_type: str,
        source_dialect: Dialect,
        ws: WorkspaceClient,
        spark: SparkSession,
        metadata_config: ReconcileMetadataConfig = ReconcileMetadataConfig(),
    ):
        self.source_connection = source_connection
        self.target_connection = target_connection
        self.recon_id = recon_id
        self.report_type = report_type
        self.source_dialect = source_dialect
        self.ws = ws
        self.spark = spark
        self._db_prefix = f"{metadata_config.catalog}.{metadata_config.schema}"

    def _generate_recon_main_id(
        self,
        table_conf: Table,
    ) -> int:
        full_source_table = f"{self.source_connection.catalog}.{self.source_connection.schema}.{table_conf.source_name}"
        full_target_table = f"{self.target_connection.catalog}.{self.target_connection.schema}.{table_conf.target_name}"
        return hash(f"{self.recon_id}{full_source_table}{full_target_table}")

    def _insert_into_main_table(
        self,
        recon_table_id: int,
        table_conf: Table,
        recon_process_duration: ReconcileProcessDuration,
        operation_name: str = "reconcile",
    ) -> None:
        source_dialect_key = get_key_from_dialect(self.source_dialect)
        df = self.spark.sql(f"""
                select {recon_table_id} as recon_table_id,
                '{self.recon_id}' as recon_id,
                case
                    when '{source_dialect_key}' = 'databricks' then 'Databricks'
                    when '{source_dialect_key}' = 'snowflake' then 'Snowflake'
                    when '{source_dialect_key}' = 'oracle' then 'Oracle'
                    when '{source_dialect_key}' = 'bigquery' then 'BigQuery'
                    else '{source_dialect_key}'
                end as source_type,
                named_struct(
                    'catalog', '{self.source_connection.catalog}',
                    'schema', '{self.source_connection.schema}',
                    'table_name', '{table_conf.source_name}'
                ) as source_table,
                named_struct(
                    'catalog', '{self.target_connection.catalog}',
                    'schema', '{self.target_connection.schema}',
                    'table_name', '{table_conf.target_name}'
                ) as target_table,
                '{self.report_type}' as report_type,
                '{operation_name}' as operation_name,
                cast('{recon_process_duration.start_ts}' as timestamp) as start_ts,
                cast('{recon_process_duration.end_ts}' as timestamp) as end_ts
            """)
        _write_df_to_delta(df, f"{self._db_prefix}.{_RECON_TABLE_NAME}")

    @classmethod
    def _is_mismatch_within_threshold_limits(
        cls, data_reconcile_output: DataReconcileOutput, table_conf: Table, record_count: ReconcileRecordCount
    ):
        total_mismatch_count = (
            data_reconcile_output.mismatch_count + data_reconcile_output.threshold_output.threshold_mismatch_count
        )
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

    @staticmethod
    def fingerprint_metrics_struct_sql(metadata: FingerprintRunMetadata) -> str:
        """Render the ``fingerprint_metrics`` named_struct for the metrics table.

        The per-field rendering is driven by ``FP_METRICS_STRUCT_FIELDS``;
        adding a metadata field is one entry in that tuple — no untyped
        f-string append. Every value flows through ``render_fp_metrics_value``
        which checks the declared SQL-type at the boundary, so raw values
        never reach the SQL string without type-aware rendering.

        Output contract:
          - ``mergeSchema`` evolves the column to a concrete StructType on
            first write (Delta can't infer fields from an all-NULL struct).
          - String fields scrubbed of embedded quotes (defense-in-depth).
          - ``None`` emits SQL ``NULL`` (not the string ``'None'``) so
            dashboards filtering on ``IS NULL`` don't miss rows.
          - Field ORDER must match the dataclass declaration; ``saveAsTable``
            resolves struct fields positionally.
        """
        parts: list[str] = []
        for sql_field, attr, sql_type in FP_METRICS_STRUCT_FIELDS:
            value = getattr(metadata, attr)
            rendered = render_fp_metrics_value(value, sql_type)
            parts.append(f"'{sql_field}', {rendered}")
        return f"named_struct({', '.join(parts)})"

    def _insert_into_metrics_table(
        self,
        recon_table_id: int,
        data_reconcile_output: DataReconcileOutput,
        schema_reconcile_output: SchemaReconcileOutput,
        table_conf: Table,
        record_count: ReconcileRecordCount,
        fingerprint_metadata: FingerprintRunMetadata | None = None,
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

        insertion_time = str(datetime.now(tz=timezone.utc))
        mismatch_columns = []
        if data_reconcile_output.mismatch and data_reconcile_output.mismatch.mismatch_columns:
            mismatch_columns = data_reconcile_output.mismatch.mismatch_columns

        # Sources that don't go through the fingerprint precheck (e.g. Snowflake,
        # Oracle today, or any aggregate-mode reconcile) don't pass metadata.
        # Use the populated "feature off" struct so dashboards can group by
        # ``eligible`` without NULL-struct handling.
        fp_metadata = fingerprint_metadata if fingerprint_metadata is not None else FingerprintRunMetadata.disabled()
        fingerprint_struct_sql = self.fingerprint_metrics_struct_sql(fp_metadata)

        df = self.spark.sql(f"""
                select {recon_table_id} as recon_table_id,
                named_struct(
                    'source_record_count', cast({record_count.source} as bigint),
                    'target_record_count', cast({record_count.target} as bigint),
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
                        {schema_reconcile_output.is_valid} else null end,
                    'fingerprint_metrics', {fingerprint_struct_sql}
                ) as recon_metrics,
                named_struct(
                    'status', {status},
                    'run_by_user', '{self.ws.current_user.me().user_name}',
                    'exception_message', "{exception_msg}"
                ) as run_metrics,
                cast('{insertion_time}' as timestamp) as inserted_ts
            """)
        # mergeSchema=True so the additive ``fingerprint_metrics`` field
        # evolves on first write against pre-existing customer tables without
        # a manual ALTER TABLE.
        _write_df_to_delta(df, f"{self._db_prefix}.{_RECON_METRICS_TABLE_NAME}", merge_schema=True)

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
            .withColumn("inserted_ts", lit(datetime.now(tz=timezone.utc)))
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

            insertion_time = str(datetime.now(tz=timezone.utc))

            # If there is any exception while running the Query,
            # each rule is stored, with the Exception message in the metrics table
            assert agg_output.rule, "Aggregate Rule must be present for storing the metrics"
            rule_id = hash(f"{recon_table_id}_{agg_output.rule.column_from_rule}")

            agg_metrics_df = self.spark.sql(f"""
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
                """)
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
        fingerprint_metadata: FingerprintRunMetadata | None = None,
    ) -> None:
        recon_table_id = self._generate_recon_main_id(table_conf)
        self._insert_into_main_table(recon_table_id, table_conf, recon_process_duration)
        self._insert_into_metrics_table(
            recon_table_id,
            data_reconcile_output,
            schema_reconcile_output,
            table_conf,
            record_count,
            fingerprint_metadata=fingerprint_metadata,
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
                .withColumn("inserted_ts", lit(datetime.now(tz=timezone.utc)))
                .select("rule_id", "rule_type", "rule_info", "inserted_ts")
            )

        if rule_df_list:
            rules_table_df = self._union_dataframes(rule_df_list)
            _write_df_to_delta(rules_table_df, f"{self._db_prefix}.{_RECON_AGGREGATE_RULES_TABLE_NAME}")
