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
        master_schema = source_schema
        if table_conf.select_columns:
            master_schema = [schema for schema in master_schema if schema.column_name in table_conf.select_columns]
        if table_conf.drop_columns:
            master_schema = [sschema for sschema in master_schema if sschema.column_name not in table_conf.drop_columns]

        target_column_map = table_conf.to_src_col_map or {}
        master_schema_match_res = [
            SchemaMatchResult(
                source_column_normalized=s.source_normalized_column_name,
                source_column_normalized_ansi=s.ansi_normalized_column_name,
                source_datatype=s.data_type,
                databricks_column=target_column_map.get(s.ansi_normalized_column_name, s.ansi_normalized_column_name),
                databricks_datatype=next(
                    (
                        tgt.data_type
                        for tgt in databricks_schema
                        if tgt.ansi_normalized_column_name
                        == target_column_map.get(s.ansi_normalized_column_name, s.ansi_normalized_column_name)
                    ),
                    "",
                ),
            )
            for s in master_schema
        ]
        return master_schema_match_res

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
    def _parse(cls, source: Dialect, column: str, data_type: str) -> str:
        return (
            parse_one(f"create table dummy ({column} {data_type})", read=source)
            .sql(dialect=get_dialect("databricks"))
            .replace(", ", ",")
        )

    @classmethod
    def _table_schema_status(cls, schema_compare_maps: list[SchemaMatchResult]) -> bool:
        return bool(all(x.is_valid for x in schema_compare_maps))

    @classmethod
    def _validate_parsed_query(cls, master: SchemaMatchResult, parsed_query) -> None:
        databricks_query = f"create table dummy ({master.source_column_normalized_ansi} {master.databricks_datatype})"
        logger.info(
            f"""
        Source datatype: create table dummy ({master.source_column_normalized} {master.source_datatype})
        Parse datatype: {parsed_query}
        Databricks datatype: {databricks_query}
        """
        )
        if parsed_query.lower() != databricks_query.lower():
            master.is_valid = False

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
                parsed_query = self._parse(source, master.source_column_normalized, master.source_datatype)
                self._validate_parsed_query(master, parsed_query)
            elif master.source_datatype.lower() != master.databricks_datatype.lower():
                master.is_valid = False

        df = self._create_output_dataframe(master_schema, self._schema_compare_output_schema)
        final_result = self._table_schema_status(master_schema)
        return SchemaReconcileOutput(final_result, df)
