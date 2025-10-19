from conftest import table_schema
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.design.normalizers import ExternalColumnDefinition, DialectNormalizer, \
    NormalizersRegistry
from databricks.labs.lakebridge.reconcile.design.utypes import ColumnTypeName, ExternalType


class Queries:
    registry: NormalizersRegistry

    def table_columns_def(self, source: DataSource, table: str):
        schema = source.get_schema("catalog", "schema", table, False)
        return [ExternalColumnDefinition(s.column_name, ExternalType(ColumnTypeName(s.data_type))) for s in schema]

    def hashed_data(self, source: DataSource, normalizer: DialectNormalizer, table: str):
        columns = self.table_columns_def(source, table)
        normalized = [normalizer.normalize(c, self.registry) for c in columns]

