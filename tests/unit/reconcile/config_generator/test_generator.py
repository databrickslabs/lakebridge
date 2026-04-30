import logging

from databricks.labs.lakebridge.reconcile.config_generator.generator import generate_table_recon
from databricks.labs.lakebridge.reconcile.recon_config import ColumnMapping, Table

from tests.conftest import schema_fixture_factory


def test_generate_table_recon_matches_tables_by_name(make_data_source):
    source = make_data_source(
        tables={("src_cat", "src_schema"): ["employees", "ORDERS"]},
        columns={
            ("src_cat", "src_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
            ("src_cat", "src_schema", "ORDERS"): [schema_fixture_factory("order_id", "int")],
        },
    )
    target = make_data_source(
        tables={("tgt_cat", "tgt_schema"): ["employees", "orders"]},
        columns={
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


def test_generate_table_recon_emits_column_mapping_only_when_names_differ(make_data_source):
    source = make_data_source(
        tables={("src_cat", "src_schema"): ["employees"]},
        columns={
            ("src_cat", "src_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("emp-name", "string"),
            ],
        },
    )
    target = make_data_source(
        tables={("tgt_cat", "tgt_schema"): ["employees"]},
        columns={
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


def test_generate_table_recon_omits_unmatched_source_tables(make_data_source, caplog):
    source = make_data_source(
        tables={("src_cat", "src_schema"): ["employees", "unrelated"]},
        columns={
            ("src_cat", "src_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
        },
    )
    target = make_data_source(
        tables={("tgt_cat", "tgt_schema"): ["employees"]},
        columns={
            ("tgt_cat", "tgt_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
        },
    )

    with caplog.at_level(logging.WARNING):
        recon = generate_table_recon(
            source=source,
            source_catalog="src_cat",
            source_schema="src_schema",
            target=target,
            target_catalog="tgt_cat",
            target_schema="tgt_schema",
        )

    assert [t.source_name for t in recon.tables] == ["employees"]
    warnings = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    assert any("unrelated" in msg for msg in warnings)


def test_generate_table_recon_logs_unmatched_columns(make_data_source, caplog):
    source = make_data_source(
        tables={("src_cat", "src_schema"): ["employees"]},
        columns={
            ("src_cat", "src_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("legacy_only", "string"),
            ],
        },
    )
    target = make_data_source(
        tables={("tgt_cat", "tgt_schema"): ["employees"]},
        columns={
            ("tgt_cat", "tgt_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
        },
    )

    with caplog.at_level(logging.WARNING):
        generate_table_recon(
            source=source,
            source_catalog="src_cat",
            source_schema="src_schema",
            target=target,
            target_catalog="tgt_cat",
            target_schema="tgt_schema",
        )

    warnings = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    assert any("legacy_only" in msg for msg in warnings)
