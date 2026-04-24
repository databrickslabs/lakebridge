from databricks.labs.lakebridge.reconcile.config_generator.generator import generate_table_recon
from databricks.labs.lakebridge.reconcile.connectors.data_source import MockDataSource
from databricks.labs.lakebridge.reconcile.recon_config import ColumnMapping, Table

from tests.conftest import schema_fixture_factory


def _mock(
    *,
    schemas: dict[str, list[str]] | None = None,
    tables: dict[tuple[str, str], list[str]] | None = None,
    schema: dict[tuple[str, str, str], list] | None = None,
) -> MockDataSource:
    return MockDataSource(
        dataframe_repository={},
        schema_repository=schema or {},
        schemas_repository=schemas or {},
        tables_repository=tables or {},
    )


def test_generate_table_recon_matches_tables_by_name():
    source = _mock(
        tables={("src_cat", "src_schema"): ["employees", "ORDERS"]},
        schema={
            ("src_cat", "src_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
            ("src_cat", "src_schema", "ORDERS"): [schema_fixture_factory("order_id", "int")],
        },
    )
    target = _mock(
        tables={("tgt_cat", "tgt_schema"): ["employees", "orders"]},
        schema={
            ("tgt_cat", "tgt_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
            ("tgt_cat", "tgt_schema", "orders"): [schema_fixture_factory("order_id", "int")],
        },
    )

    recon = generate_table_recon(
        source=source,
        source_catalog="src_cat",
        source_schema="src_schema",
        target=target,
        target_catalog="tgt_cat",
        target_schema="tgt_schema",
    )

    assert recon.tables == [
        Table(source_name="employees", target_name="employees"),
        Table(source_name="orders", target_name="orders"),
    ]


def test_generate_table_recon_emits_column_mapping_only_when_names_differ():
    source = _mock(
        tables={("src_cat", "src_schema"): ["employees"]},
        schema={
            ("src_cat", "src_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("emp-name", "string"),
            ],
        },
    )
    target = _mock(
        tables={("tgt_cat", "tgt_schema"): ["employees"]},
        schema={
            ("tgt_cat", "tgt_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("emp_name", "string"),
            ],
        },
    )

    recon = generate_table_recon(
        source=source,
        source_catalog="src_cat",
        source_schema="src_schema",
        target=target,
        target_catalog="tgt_cat",
        target_schema="tgt_schema",
    )

    assert recon.tables == [
        Table(
            source_name="employees",
            target_name="employees",
            column_mapping=[ColumnMapping(source_name="emp-name", target_name="emp_name")],
        ),
    ]


def test_generate_table_recon_omits_unmatched_source_tables(caplog):
    source = _mock(
        tables={("src_cat", "src_schema"): ["employees", "unrelated"]},
        schema={
            ("src_cat", "src_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
        },
    )
    target = _mock(
        tables={("tgt_cat", "tgt_schema"): ["employees"]},
        schema={
            ("tgt_cat", "tgt_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
        },
    )

    with caplog.at_level("WARNING"):
        recon = generate_table_recon(
            source=source,
            source_catalog="src_cat",
            source_schema="src_schema",
            target=target,
            target_catalog="tgt_cat",
            target_schema="tgt_schema",
        )

    assert [t.source_name for t in recon.tables] == ["employees"]
    assert "unrelated" in caplog.text


def test_generate_table_recon_logs_unmatched_columns(caplog):
    source = _mock(
        tables={("src_cat", "src_schema"): ["employees"]},
        schema={
            ("src_cat", "src_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("legacy_only", "string"),
            ],
        },
    )
    target = _mock(
        tables={("tgt_cat", "tgt_schema"): ["employees"]},
        schema={
            ("tgt_cat", "tgt_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
        },
    )

    with caplog.at_level("WARNING"):
        generate_table_recon(
            source=source,
            source_catalog="src_cat",
            source_schema="src_schema",
            target=target,
            target_catalog="tgt_cat",
            target_schema="tgt_schema",
        )

    assert "legacy_only" in caplog.text
