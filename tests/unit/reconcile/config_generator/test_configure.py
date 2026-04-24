import json
from dataclasses import asdict
from unittest.mock import MagicMock, create_autospec, patch

from databricks.labs.blueprint.installation import Installation
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import (
    ReconcileConfig,
    ReconcileMetadataConfig,
    SourceConnectionConfig,
    TableRecon,
    TargetConnectionConfig,
)
from databricks.labs.lakebridge.reconcile.config_generator.configure import configure_tables, draft_filename
from databricks.labs.lakebridge.reconcile.connectors.data_source import MockDataSource
from databricks.labs.lakebridge.reconcile.recon_config import Table

from tests.conftest import schema_fixture_factory


def _reconcile_config(
    *,
    dialect: str = "snowflake",
    catalog: str = "src_cat",
    schema: str = "src_schema",
    uc_connection_name: str | None = "my_conn",
    target_catalog: str = "tgt_cat",
    target_schema: str = "tgt_schema",
    report_type: str = "all",
) -> ReconcileConfig:
    return ReconcileConfig(
        report_type=report_type,
        source=SourceConnectionConfig(
            dialect=dialect,
            catalog=catalog,
            schema=schema,
            uc_connection_name=uc_connection_name,
        ),
        target=TargetConnectionConfig(catalog=target_catalog, schema=target_schema),
        metadata_config=ReconcileMetadataConfig(catalog="meta_cat", schema="meta_schema", volume="meta_vol"),
    )


def test_draft_filename_uses_uc_connection_when_set():
    config = _reconcile_config(dialect="snowflake", uc_connection_name="my_conn", report_type="all")
    assert draft_filename(config) == "recon_config_snowflake_my_conn_all.json"


def test_draft_filename_falls_back_to_catalog_for_databricks():
    config = _reconcile_config(dialect="databricks", catalog="hive_metastore", uc_connection_name=None)
    assert draft_filename(config) == "recon_config_databricks_hive_metastore_all.json"


def test_configure_tables_uploads_draft_to_canonical_filename():
    source_ds = MockDataSource(
        dataframe_repository={},
        schema_repository={
            ("src_cat", "src_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
        },
        schemas_repository={"src_cat": ["src_schema"]},
        tables_repository={("src_cat", "src_schema"): ["employees"]},
    )
    target_ds = MockDataSource(
        dataframe_repository={},
        schema_repository={
            ("tgt_cat", "tgt_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
        },
        schemas_repository={"tgt_cat": ["tgt_schema"]},
        tables_repository={("tgt_cat", "tgt_schema"): ["employees"]},
    )

    ws = create_autospec(WorkspaceClient)
    installation = create_autospec(Installation)
    config = _reconcile_config()

    with patch(
        "databricks.labs.lakebridge.reconcile.config_generator.configure.create_adapter",
        side_effect=[source_ds, target_ds],
    ):
        result = configure_tables(
            ws=ws,
            installation=installation,
            reconcile_config=config,
            spark=MagicMock(),
        )

    assert result.tables == [Table(source_name="employees", target_name="employees")]

    installation.upload.assert_called_once()
    filename, payload = installation.upload.call_args.args
    assert filename == "recon_config_snowflake_my_conn_all.json"
    parsed = json.loads(payload.decode())
    assert parsed == asdict(TableRecon(tables=[Table(source_name="employees", target_name="employees")]))
