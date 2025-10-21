import dataclasses
from abc import ABC

from pyspark.sql import DataFrame
from pyspark.sql.functions import col

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.design.expressions import HashExpressionsBuilder, DialectType, QueryBuilder
from databricks.labs.lakebridge.reconcile.design.normalizers import DialectNormalizer, ExternalColumnDefinition, \
    NormalizersRegistry
from databricks.labs.lakebridge.reconcile.design.utypes import ExternalType, ColumnTypeName
from databricks.labs.lakebridge.reconcile.recon_config import Table, Schema


@dataclasses.dataclass
class ReconcileOutput:
    status: str
    name: str
    recon_id: str
    details: dict

    def __init__(self, status: bool, name: str, recon_id: str, details: dict):
        self.status = "success" if status else "failure"
        self.name = name
        self.recon_id = recon_id
        self.details = details

@dataclasses.dataclass
class ReconcileLayerContext:
    layer: str
    dialect: DialectType
    source: DataSource
    normalizer: DialectNormalizer

@dataclasses.dataclass
class ReconcileRequest:
    recon_id: str
    reconcile_type: str
    source_context: ReconcileLayerContext
    target_context: ReconcileLayerContext
    config: Table

class Reconcile:

    def reconcile(self, config: ReconcileRequest) -> ReconcileOutput:
        pass

class ComparisonStrategy(ABC):
    name: str
    registry: NormalizersRegistry

    def query_source_data(self, config: ReconcileRequest) -> DataFrame:
        pass

    def query_target_data(self, config: ReconcileRequest) -> DataFrame:
        pass

    def compare_data(self, config: ReconcileRequest, source_data: DataFrame, target_data: DataFrame) -> ReconcileOutput:
        pass

    def execute(self, config: ReconcileRequest) -> ReconcileOutput:
        return self.compare_data(
            config,
            self.query_source_data(config),
            self.query_target_data(config)
        )

    @classmethod
    def get_strategy(cls, name: str) -> "ComparisonStrategy":
        strategies = {
            "row": HashBasedComparisonStrategy,
            "aggregate": AggregateComparisonStrategy,
            "schema": SchemaComparisonStrategy,
            "data": DataComparisonStrategy,
            "all": AllComparisonsStrategy,
        }
        strategy_class = strategies.get(name.lower())
        if not strategy_class:
            raise ValueError(f"Unknown reconcile strategy name: {name}")
        return strategy_class()

class HashBasedComparisonStrategy(ComparisonStrategy):
    name = "row"

    hash_column_name = "hash_value_recon"

    def build_query(self, layer: ReconcileLayerContext, schema: list[Schema], config: Table):
        columns =  [ExternalColumnDefinition(s.column_name, ExternalType(ColumnTypeName(s.data_type))) for s in schema]
        normalized = [layer.normalizer.normalize(c) for c in columns]

        join_columns = [jc for jc in normalized if jc.column_name in config.join_columns]
        hashed = HashExpressionsBuilder(layer.dialect, normalized).alias(self.hash_column_name)
        select_cols = join_columns.copy()
        select_cols.append(hashed)
        query = QueryBuilder(layer.dialect, select_cols)
        return query

    def query_source_data(self, config: ReconcileRequest) -> DataFrame:
        source_table_fqn = ".".join(["catalog", "schema", config.config.source_name]) # FIXME implement
        schema = config.source_context.source.get_schema(None, "", source_table_fqn, False)
        query = self.build_query(config.source_context, schema, config.config)
        df = config.source_context.source.read_data(None, "", source_table_fqn, query.build(), config.config.jdbc_reader_options)
        df = df.alias("src")
        return df

    def query_target_data(self, config: ReconcileRequest) -> DataFrame:
        target_table_fqn = ".".join(["catalog", "schema", config.config.target_name]) # FIXME implement
        schema = config.source_context.source.get_schema(None, "", target_table_fqn, False)
        query = self.build_query(config.target_context, schema, config.config)
        df = config.source_context.source.read_data(None, "", target_table_fqn, query.build(), config.config.jdbc_reader_options)
        df = df.alias("tgt")
        return df

    def compare_data(self, config: ReconcileRequest, source_data: DataFrame, target_data: DataFrame) -> ReconcileOutput:
        joined = source_data.join(target_data,
                         col(f"src.{self.hash_column_name}") == col(f"tgt.{self.hash_column_name}"),
                         'outer')

        successful = joined.count() == source_data.count() == target_data.count() # Very basic comparison for now
        sample = max(1.0, 100/joined.count())
        return ReconcileOutput(successful, self.name, config.recon_id, {"sample_output": joined.sample(sample, 42).collect()})

class AggregateComparisonStrategy(ComparisonStrategy):
    name = "aggregate"

    def query_source_data(self, config: ReconcileRequest) -> DataFrame:
        pass

    def query_target_data(self, config: ReconcileRequest) -> DataFrame:
        pass

    def compare_data(self, source_data: DataFrame, target_data: DataFrame) -> ReconcileOutput:
        pass

class SchemaComparisonStrategy(ComparisonStrategy):
    name = "schema"

    def query_source_data(self, config: ReconcileRequest) -> DataFrame:
        pass

    def query_target_data(self, config: ReconcileRequest) -> DataFrame:
        pass

    def compare_data(self, source_data: DataFrame, target_data: DataFrame) -> ReconcileOutput:
        pass

class DataComparisonStrategy(ComparisonStrategy):
    name = "data"

    def execute(self, config: ReconcileRequest) -> ReconcileOutput:
        pass

class AllComparisonsStrategy(ComparisonStrategy):
    name = "all"

    def execute(self, config: ReconcileRequest) -> ReconcileOutput:
        schema = SchemaComparisonStrategy().execute(config)
        row = HashBasedComparisonStrategy().execute(config)
        column = DataComparisonStrategy().execute(config)

        return ReconcileOutput(
            status=(
                self._map_status(schema.status)
                and self._map_status(row.status)
                and self._map_status(column.status)
            ),
            name="all",
            recon_id=config.recon_id,
            details={
                "schema": schema.details,
                "row": row.details,
                "column": column.details
            }
        )

    @classmethod
    def _map_status(cls, status: str) -> bool:
        return status.lower() == "success"
