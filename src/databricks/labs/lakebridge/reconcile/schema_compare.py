import logging

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.types import BooleanType, StringType, StructField, StructType
from sqlglot import Dialect, parse_one

from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.recon_config import Schema, Table
from databricks.labs.lakebridge.reconcile.recon_output_config import SchemaMatchResult, SchemaReconcileOutput
from databricks.labs.lakebridge.transpiler.sqlglot.generator.databricks import Databricks

logger = logging.getLogger(__name__)


class SchemaCompare:
    def __init__(
        self,
        spark: SparkSession,
    ):
        self.spark = spark

    _schema_compare_output_schema: StructType = StructType(
        [
            StructField("source_column", StringType(), False),
            StructField("source_datatype", StringType(), False),
            StructField("databricks_column", StringType(), True),
            StructField("databricks_datatype", StringType(), True),
            StructField("is_valid", BooleanType(), False),
        ]
    )

    @classmethod
    def _build_master_schema(
        cls,
        source_schema: list[Schema],
        databricks_schema: list[Schema],
        table_conf: Table,
    ) -> list[SchemaMatchResult]:
        master_schema = SchemaCompare._select_columns(source_schema, table_conf)
        master_schema = SchemaCompare._drop_columns(master_schema, table_conf)

        target_column_map = table_conf.to_src_col_map or {}
        databricks_types_map = {c.ansi_normalized_column_name: c.data_type for c in databricks_schema}

        master_schema_match_res = [
            SchemaCompare.match_source_target_schemas(s, target_column_map, databricks_types_map) for s in master_schema
        ]

        return master_schema_match_res

    @staticmethod
    def _select_columns(master_schema: list[Schema], table_conf: Table):
        if table_conf.select_columns:
            return [schema for schema in master_schema if schema.column_name in table_conf.select_columns]
        return master_schema

    @staticmethod
    def _drop_columns(master_schema: list[Schema], table_conf: Table):
        if table_conf.drop_columns:
            return [sschema for sschema in master_schema if sschema.column_name not in table_conf.drop_columns]
        return master_schema

    @staticmethod
    def match_source_target_schemas(
        schema: Schema, target_column_map: dict, databricks_schema_map: dict
    ) -> SchemaMatchResult:
        databricks_column_name = target_column_map.get(
            schema.ansi_normalized_column_name, schema.ansi_normalized_column_name
        )
        databricks_datatype = databricks_schema_map.get(databricks_column_name, "Unknown")

        return SchemaMatchResult(
            source_column_normalized=schema.source_normalized_column_name,
            source_column_normalized_ansi=schema.ansi_normalized_column_name,
            source_datatype=schema.data_type,
            databricks_column=databricks_column_name,
            databricks_datatype=databricks_datatype,
        )

    def _create_output_dataframe(self, data: list[SchemaMatchResult], schema: StructType) -> DataFrame:
        """Return a user-friendly dataframe for schema compare result."""
        transformed = []
        for item in data:
            output = tuple(
                [
                    DialectUtils.unnormalize_identifier(item.source_column_normalized_ansi),
                    item.source_datatype,
                    DialectUtils.unnormalize_identifier(item.databricks_column),
                    item.databricks_datatype,
                    item.is_valid,
                ]
            )
            transformed.append(output)

        return self.spark.createDataFrame(transformed, schema)

    @classmethod
    def _table_schema_status(cls, schema_compare_maps: list[SchemaMatchResult]) -> bool:
        return bool(all(x.is_valid for x in schema_compare_maps))

    @classmethod
    def _validate_parsed_query(cls, source: Dialect, master: SchemaMatchResult) -> None:
        """
        Reconcile the schema of a single column by comparing the source column and the databricks column.

        1. This works by creating two SQL queries. both queries are a create table statement with a single column:
            * first query uses the source column name and datatype to simulate the source.
            * second query uses the same column name as ansi and databricks datatype to simulate databricks.
            * we don't use the databricks column name as it may have been renamed.
            * renaming is checked in the previous step to retrieve the databricks column.
        2. Parse both queries using sqlglot and convert both to the other dialect
        3. Compare the converted queries with our original queries.
        4. If neither of the checks succeed, the column is marked as invalid

        :param source: source dialect e.g. TSQL, Oracle, Snowflake etc.
        :param master: source and target column names and datatypes computed by previous step.
        """
        target = get_dialect("databricks")
        source_query = f"create table dummy ({master.source_column_normalized} {master.source_datatype})"
        converted_source_query = cls._parse(source, target, source_query)
        databricks_query = f"create table dummy ({master.source_column_normalized_ansi} {master.databricks_datatype})"
        converted_databricks_query = cls._parse(target, source, databricks_query)
        parsed_source_check = converted_source_query.lower() == databricks_query.lower()
        parsed_databricks_check = source_query.lower() == converted_databricks_query.lower()
        logger.info(
            f"""
        Source query: {source_query}
        Converted source query: {converted_source_query}
        Databricks query: {databricks_query}
        Converted databricks query: {converted_databricks_query}
        Source equality check: {parsed_source_check}
        Databricks equality check: {parsed_databricks_check}
        """
        )

        if not parsed_source_check and not parsed_databricks_check:
            master.is_valid = False

    @classmethod
    def _parse(cls, source: Dialect, target: Dialect, source_query: str) -> str:
        return parse_one(source_query, read=source).sql(dialect=target).replace(", ", ",")

    def compare(
        self,
        source_schema: list[Schema],
        databricks_schema: list[Schema],
        source: Dialect,
        table_conf: Table,
    ) -> SchemaReconcileOutput:
        """
        This method compares the source schema and the Databricks schema. It checks if the data types of the columns in the source schema
        match with the corresponding columns in the Databricks schema by parsing using remorph transpile.

        Returns:
            SchemaReconcileOutput: A dataclass object containing a boolean indicating the overall result of the comparison and a DataFrame with the comparison details.
        """
        master_schema = self._build_master_schema(source_schema, databricks_schema, table_conf)
        for master in master_schema:
            if not isinstance(source, Databricks):
                self._validate_parsed_query(source, master)
            elif master.source_datatype.lower() != master.databricks_datatype.lower():
                master.is_valid = False

        df = self._create_output_dataframe(master_schema, self._schema_compare_output_schema)
        final_result = self._table_schema_status(master_schema)
        return SchemaReconcileOutput(final_result, df)
