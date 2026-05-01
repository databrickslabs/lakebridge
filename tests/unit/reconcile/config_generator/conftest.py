import pytest

from databricks.labs.lakebridge.config import (
    ReconcileConfig,
    ReconcileMetadataConfig,
    SourceConnectionConfig,
    TargetConnectionConfig,
)
from databricks.labs.lakebridge.reconcile.connectors.data_source import MockDataSource
from databricks.labs.lakebridge.reconcile.recon_config import Schema


@pytest.fixture
def make_data_source():
    def _factory(
        tables: dict[tuple[str, str], list[str]] | None = None,
        columns: dict[tuple[str, str, str], list[Schema]] | None = None,
    ) -> MockDataSource:
        return MockDataSource(
            dataframe_repository={},
            schema_repository=columns or {},
            schemas_repository={},
            tables_repository=tables or {},
        )

    return _factory


@pytest.fixture
def reconcile_config():
    def _factory(**kwargs) -> ReconcileConfig:
        return ReconcileConfig(
            report_type=kwargs.get("report_type", "all"),
            source=SourceConnectionConfig(
                dialect=kwargs.get("dialect", "snowflake"),
                catalog=kwargs.get("catalog", "src_cat"),
                schema=kwargs.get("schema", "src_schema"),
                uc_connection_name=kwargs.get("uc_connection_name", "my_conn"),
            ),
            target=TargetConnectionConfig(
                catalog=kwargs.get("target_catalog", "tgt_cat"),
                schema=kwargs.get("target_schema", "tgt_schema"),
            ),
            metadata_config=ReconcileMetadataConfig(catalog="meta_cat", schema="meta_schema", volume="meta_vol"),
        )

    return _factory
