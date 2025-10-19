import dataclasses
from abc import ABC

from pyspark.sql import DataFrame

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.design.normalizers import DialectNormalizer


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
    source: DataSource
    normalizer: DialectNormalizer

@dataclasses.dataclass
class ReconcileRequest:
    recon_id: str
    reconcile_type: str
    source_context: ReconcileLayerContext
    target_context: ReconcileLayerContext

class Reconcile:

    def reconcile(self, config: ReconcileRequest) -> ReconcileOutput:
        pass

class ComparisonStrategy(ABC):
    name: str

    def query_source_data(self, config: ReconcileRequest) -> DataFrame:
        pass

    def query_target_data(self, config: ReconcileRequest) -> DataFrame:
        pass

    def compare_data(self, source_data: DataFrame, target_data: DataFrame) -> ReconcileOutput:
        pass

    def execute(self, config: ReconcileRequest) -> ReconcileOutput:
        return self.compare_data(
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

    def query_source_data(self, config: ReconcileRequest) -> DataFrame:

        pass

    def query_target_data(self, config: ReconcileRequest) -> DataFrame:
        pass

    def compare_data(self, source_data: DataFrame, target_data: DataFrame) -> ReconcileOutput:
        pass

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
