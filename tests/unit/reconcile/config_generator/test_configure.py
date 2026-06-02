import json
from dataclasses import asdict
from unittest.mock import create_autospec

from databricks.labs.blueprint.installation import Installation

from databricks.labs.lakebridge.config import TableRecon
from databricks.labs.lakebridge.reconcile.config_generator.configure import ColumnMappingAutoConfigurer
from databricks.labs.lakebridge.reconcile.config_generator.execute import auto_configure_tables
from databricks.labs.lakebridge.reconcile.recon_config import ColumnMapping, Table

from tests.conftest import schema_fixture_factory


def _configure(installation, source_ds, target_ds, *, auto_configurers, table_recon=None):
    """Test helper — pass the two adapters and the configurers list directly (DI, no patching)."""
    return auto_configure_tables(
        installation=installation,
        table_recon_filename="recon_config.json",
        source=source_ds,
        source_catalog="src_cat",
        source_schema="src_schema",
        target=target_ds,
        target_catalog="tgt_cat",
        target_schema="tgt_schema",
        auto_configurers=auto_configurers,
        table_recon=table_recon,
    )


def test_uploads_to_canonical_filename(make_data_source):
    source_ds = make_data_source(
        tables={("src_cat", "src_schema"): ["employees"]},
        columns={("src_cat", "src_schema", "employees"): [schema_fixture_factory("emp_id", "int")]},
    )
    target_ds = make_data_source(
        tables={("tgt_cat", "tgt_schema"): ["employees"]},
        columns={("tgt_cat", "tgt_schema", "employees"): [schema_fixture_factory("emp_id", "int")]},
    )
    installation = create_autospec(Installation)

    result = _configure(installation, source_ds, target_ds, auto_configurers=[ColumnMappingAutoConfigurer()])

    assert result.tables == [Table(source_name="employees", target_name="employees")]
    filename, payload = installation.upload.call_args.args
    assert filename == "recon_config.json"
    assert json.loads(payload.decode()) == asdict(
        TableRecon(tables=[Table(source_name="employees", target_name="employees")])
    )


def test_empty_configurers_discovers_only(make_data_source):
    """No configurers → just discovery; column_mapping stays None."""
    source_ds = make_data_source(tables={("src_cat", "src_schema"): ["employees"]})
    target_ds = make_data_source(tables={("tgt_cat", "tgt_schema"): ["employees"]})
    installation = create_autospec(Installation)

    result = _configure(installation, source_ds, target_ds, auto_configurers=[])

    assert result.tables == [Table(source_name="employees", target_name="employees")]
    assert result.tables[0].column_mapping is None


def test_applies_configurers_in_declared_order_table_outer(make_data_source):
    """Loop order: outer = tables, inner = configurers."""

    class _Marker:
        def __init__(self, name: str, log: list[tuple[str, str]]) -> None:
            self._name, self._log = name, log

        def configure(self, *, table, **_):
            self._log.append((table.source_name, self._name))
            return table

    source_ds = make_data_source(tables={("src_cat", "src_schema"): ["alpha", "beta"]})
    target_ds = make_data_source(tables={("tgt_cat", "tgt_schema"): ["alpha", "beta"]})
    installation = create_autospec(Installation)

    log: list[tuple[str, str]] = []
    _configure(installation, source_ds, target_ds, auto_configurers=[_Marker("first", log), _Marker("second", log)])

    assert log == [
        ("alpha", "first"),
        ("alpha", "second"),
        ("beta", "first"),
        ("beta", "second"),
    ]


def test_column_mapping_configurer_emits_mappings(make_data_source):
    source_ds = make_data_source(
        tables={("src_cat", "src_schema"): ["employees"]},
        columns={
            ("src_cat", "src_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("emp-name", "string"),
            ],
        },
    )
    target_ds = make_data_source(
        tables={("tgt_cat", "tgt_schema"): ["employees"]},
        columns={
            ("tgt_cat", "tgt_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("emp_name", "string"),
            ],
        },
    )
    installation = create_autospec(Installation)

    result = _configure(installation, source_ds, target_ds, auto_configurers=[ColumnMappingAutoConfigurer()])

    assert result.tables == [
        Table(
            source_name="employees",
            target_name="employees",
            column_mapping=[ColumnMapping(source_name="emp-name", target_name="emp_name")],
        ),
    ]


def test_reuses_existing_table_recon_when_provided(make_data_source):
    """When `table_recon` is passed, skip discovery and apply configurers to it."""
    source_ds = make_data_source(
        columns={
            ("src_cat", "src_schema", "preserved"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("emp-name", "string"),
            ],
        },
    )
    target_ds = make_data_source(
        columns={
            ("tgt_cat", "tgt_schema", "preserved"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("emp_name", "string"),
            ],
        },
    )
    installation = create_autospec(Installation)
    existing = TableRecon(tables=[Table(source_name="preserved", target_name="preserved")])

    result = _configure(
        installation,
        source_ds,
        target_ds,
        auto_configurers=[ColumnMappingAutoConfigurer()],
        table_recon=existing,
    )

    assert result.tables == [
        Table(
            source_name="preserved",
            target_name="preserved",
            column_mapping=[ColumnMapping(source_name="emp-name", target_name="emp_name")],
        ),
    ]
