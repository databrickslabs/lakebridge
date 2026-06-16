from databricks.labs.blueprint.installation import MockInstallation

from databricks.labs.lakebridge.config import (
    ReconcileConfig,
    ReconcileMetadataConfig,
    SourceConnectionConfig,
    TableRecon,
    TargetConnectionConfig,
    TranspileConfig,
)
from databricks.labs.lakebridge.reconcile.recon_config import Table


def _reconcile_config(
    *,
    dialect: str = "snowflake",
    catalog: str = "src_cat",
    uc_connection_name: str | None = "my_conn",
    report_type: str = "all",
) -> ReconcileConfig:
    return ReconcileConfig(
        report_type=report_type,
        source=SourceConnectionConfig(
            dialect=dialect,
            catalog=catalog,
            schema="src_schema",
            uc_connection_name=uc_connection_name,
        ),
        target=TargetConnectionConfig(catalog="tgt_cat", schema="tgt_schema"),
        metadata_config=ReconcileMetadataConfig(catalog="meta_cat", schema="meta_schema", volume="meta_vol"),
    )


def test_table_recon_filename_uses_uc_connection_when_set() -> None:
    config = _reconcile_config(dialect="snowflake", uc_connection_name="my_conn", report_type="all")
    assert config.table_recon_filename == "recon_config_snowflake_my_conn_all.json"


def test_table_recon_filename_falls_back_to_catalog_for_databricks() -> None:
    config = _reconcile_config(dialect="databricks", catalog="hive_metastore", uc_connection_name=None)
    assert config.table_recon_filename == "recon_config_databricks_hive_metastore_all.json"


def test_transpiler_config_default_serialization() -> None:
    """Verify that the default TranspileConfig can be serialized and deserialized correctly."""
    config = TranspileConfig()

    installation = MockInstallation()
    installation.save(config)

    loaded = installation.load(TranspileConfig)
    assert loaded == config


def test_typical_transpiler_config() -> None:
    """Verify that a typical TranspileConfig dataclass can be saved and loaded correctly."""
    config = TranspileConfig(
        transpiler_config_path="/a/path/to/config.yml",
        source_dialect="value with spaces",
        input_source="/a/path",
        output_folder="/another/path",
        error_file_path="file_without_path.log",
        skip_validation=True,
        catalog_name="remorph",
        schema_name="transpiler",
        sdk_config={
            "foo": "bar",
            "not_a_number": "1",
        },
        transpiler_options={
            "overrides-file": None,
            "something_else": "value",
        },
    )

    installation = MockInstallation()
    installation.save(config)

    loaded = installation.load(TranspileConfig)
    assert loaded == config


def test_complete_transpiler_config() -> None:
    """Verify that a complete TranspileConfig dataclass can be saved and loaded correctly."""
    config = TranspileConfig(
        transpiler_config_path="/a/path/to/config.yml",
        source_dialect="value with spaces",
        input_source="/a/path",
        output_folder="/another/path",
        error_file_path="file_without_path.log",
        skip_validation=True,
        catalog_name="remorph",
        schema_name="transpiler",
        sdk_config={
            "foo": "bar",
            "not_a_number": "1",
        },
        transpiler_options={
            "overrides-file": None,
            "nested": {"key": "value", "list": [1, 2, 3], "nested_further": {}},
            "nasty_number": 0.1,
        },
    )

    installation = MockInstallation()
    installation.save(config)

    loaded = installation.load(TranspileConfig)
    assert loaded == config


def test_reconcile_table_config_default_serialization() -> None:
    """Verify that older config that had extra keys still works"""
    config = TableRecon([Table(source_name="source1", target_name="target1")])
    installation = MockInstallation(
        {
            "recon_config.yml": {
                "tables": [
                    {
                        "source_name": "source1",
                        "target_name": "target1",
                    }
                ],
                "version": 1,
            },
        }
    )

    loaded = installation.load(TableRecon)
    assert loaded.tables == config.tables


def test_reconcile_v1_migrate_non_databricks() -> None:
    """v1 reconcile.yml for an external source migrates to v2 with a placeholder UC connection name."""
    installation = MockInstallation(
        {
            "reconcile.yml": {
                "data_source": "snowflake",
                "secret_scope": "remorph_snowflake",
                "report_type": "all",
                "database_config": {
                    "source_catalog": "snowflake_sample_data",
                    "source_schema": "tpch_sf1000",
                    "target_catalog": "tpch",
                    "target_schema": "1000gb",
                },
                "metadata_config": {"catalog": "remorph", "schema": "reconcile", "volume": "reconcile_volume"},
                "version": 1,
            }
        }
    )

    loaded = installation.load(ReconcileConfig)

    assert loaded == ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="snowflake",
            catalog="snowflake_sample_data",
            schema="tpch_sf1000",
            uc_connection_name="TODO",
        ),
        target=TargetConnectionConfig(catalog="tpch", schema="1000gb"),
        metadata_config=ReconcileMetadataConfig(catalog="remorph", schema="reconcile", volume="reconcile_volume"),
    )


def test_reconcile_v1_migrate_databricks() -> None:
    """v1 reconcile.yml for databricks source migrates without setting a UC connection name."""
    installation = MockInstallation(
        {
            "reconcile.yml": {
                "data_source": "databricks",
                "secret_scope": "remorph_databricks",
                "report_type": "all",
                "database_config": {
                    "source_catalog": "src_catalog",
                    "source_schema": "src_schema",
                    "target_catalog": "tgt_catalog",
                    "target_schema": "tgt_schema",
                },
                "metadata_config": {"catalog": "remorph", "schema": "reconcile", "volume": "reconcile_volume"},
                "version": 1,
            }
        }
    )

    loaded = installation.load(ReconcileConfig)

    assert loaded.source.dialect == "databricks"
    assert loaded.source.uc_connection_name is None
    assert loaded.source.catalog == "src_catalog"


def test_reconcile_v1_migrate_oracle_without_source_catalog() -> None:
    """In v1 source_catalog was optional for Oracle (service name); migrate defaults to ORCL."""
    installation = MockInstallation(
        {
            "reconcile.yml": {
                "data_source": "oracle",
                "secret_scope": "remorph_oracle",
                "report_type": "all",
                "database_config": {
                    "source_schema": "HR",
                    "target_catalog": "tgt_catalog",
                    "target_schema": "tgt_schema",
                },
                "metadata_config": {"catalog": "remorph", "schema": "reconcile", "volume": "reconcile_volume"},
                "version": 1,
            }
        }
    )

    loaded = installation.load(ReconcileConfig)

    assert loaded.source.dialect == "oracle"
    assert loaded.source.catalog == "ORCL"
    assert loaded.source.uc_connection_name == "TODO"


def test_reconcile_v1_migrate_drops_orphan_fields() -> None:
    """secret_scope and the legacy tables field are not retained in the migrated config."""
    installation = MockInstallation(
        {
            "reconcile.yml": {
                "data_source": "snowflake",
                "secret_scope": "remorph_snowflake",
                "report_type": "all",
                "database_config": {
                    "source_catalog": "src",
                    "source_schema": "sch",
                    "target_catalog": "tgt",
                    "target_schema": "tsch",
                },
                "tables": {"filter_type": "all", "tables_list": ["*"]},
                "metadata_config": {"catalog": "remorph", "schema": "reconcile", "volume": "reconcile_volume"},
                "version": 1,
            }
        }
    )

    # Should load cleanly without choking on orphan fields.
    loaded = installation.load(ReconcileConfig)
    assert loaded.source.dialect == "snowflake"
    assert loaded.target.catalog == "tgt"
