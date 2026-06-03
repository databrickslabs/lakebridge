import logging

from databricks.labs.lakebridge.reconcile.config_generator.configure import (
    AutoConfigureContext,
    ColumnMappingAutoConfigurer,
    TableMatcher,
)
from databricks.labs.lakebridge.reconcile.recon_config import ColumnMapping, Table

from tests.conftest import schema_fixture_factory


def _ctx(source, target, table: Table) -> AutoConfigureContext:
    return AutoConfigureContext(
        source=source,
        source_catalog="src_cat",
        source_schema="src_schema",
        source_columns=source.get_schema("src_cat", "src_schema", table.source_name),
        target=target,
        target_catalog="tgt_cat",
        target_schema="tgt_schema",
        target_columns=target.get_schema("tgt_cat", "tgt_schema", table.target_name),
    )


def test_table_matcher_returns_table_pairs_without_column_mapping(make_data_source):
    source = make_data_source(
        tables={("src_cat", "src_schema"): ["employees", "ORDERS"]},
    )
    target = make_data_source(
        tables={("tgt_cat", "tgt_schema"): ["employees", "orders"]},
    )

    recon = TableMatcher().discover(
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


def test_table_matcher_omits_unmatched_source_tables(make_data_source, caplog):
    source = make_data_source(
        tables={("src_cat", "src_schema"): ["employees", "unrelated"]},
    )
    target = make_data_source(
        tables={("tgt_cat", "tgt_schema"): ["employees"]},
    )

    with caplog.at_level(logging.WARNING, logger="databricks.labs.lakebridge.reconcile.config_generator.configure"):
        recon = TableMatcher().discover(
            source=source,
            source_catalog="src_cat",
            source_schema="src_schema",
            target=target,
            target_catalog="tgt_cat",
            target_schema="tgt_schema",
        )

    assert [t.source_name for t in recon.tables] == ["employees"]
    assert caplog.messages == ["Could not auto-match 1 source table(s); add manually: unrelated"]


def test_column_mapping_auto_configurer_emits_only_when_names_differ(make_data_source):
    """ColumnMappingAutoConfigurer fills column_mapping with entries only where source/target names differ."""
    source = make_data_source(
        columns={
            ("src_cat", "src_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("emp-name", "string"),
            ],
        },
    )
    target = make_data_source(
        columns={
            ("tgt_cat", "tgt_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("emp_name", "string"),
            ],
        },
    )

    table = Table(source_name="employees", target_name="employees")
    configured = ColumnMappingAutoConfigurer().configure(table, _ctx(source, target, table))

    assert configured == Table(
        source_name="employees",
        target_name="employees",
        column_mapping=[ColumnMapping(source_name="emp-name", target_name="emp_name")],
    )


def test_column_mapping_auto_configurer_lists_matched_in_select_columns_when_source_unmatched(make_data_source, caplog):
    """Unmatched source column: matched ones go into select_columns; drop_columns stays None."""
    source = make_data_source(
        columns={
            ("src_cat", "src_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("legacy_only", "string"),
            ],
        },
    )
    target = make_data_source(
        columns={
            ("tgt_cat", "tgt_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
        },
    )

    table = Table(source_name="employees", target_name="employees")
    with caplog.at_level(logging.WARNING, logger="databricks.labs.lakebridge.reconcile.config_generator.configure"):
        configured = ColumnMappingAutoConfigurer().configure(table, _ctx(source, target, table))

    assert configured.select_columns == ["emp_id"]
    assert configured.drop_columns is None
    assert caplog.messages == [
        "Could not auto-match 1 source column(s) for employees -> employees: legacy_only. "
        "Listed 1 matched column(s) in select_columns.",
    ]


def test_column_mapping_auto_configurer_adds_unmatched_target_to_drop_columns(make_data_source, caplog):
    """Unmatched target column: goes into drop_columns; select_columns stays None when no source unmatched."""
    source = make_data_source(
        columns={
            ("src_cat", "src_schema", "employees"): [schema_fixture_factory("emp_id", "int")],
        },
    )
    target = make_data_source(
        columns={
            ("tgt_cat", "tgt_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("target_only", "string"),
            ],
        },
    )

    table = Table(source_name="employees", target_name="employees")
    with caplog.at_level(logging.WARNING, logger="databricks.labs.lakebridge.reconcile.config_generator.configure"):
        configured = ColumnMappingAutoConfigurer().configure(table, _ctx(source, target, table))

    assert configured.select_columns is None
    assert configured.drop_columns == ["target_only"]
    assert caplog.messages == [
        "Target has 1 unmatched column(s) for employees -> employees: target_only. Added to drop_columns.",
    ]


def test_column_mapping_auto_configurer_clears_select_and_drop_when_all_match(make_data_source):
    """When every column matches on both sides, select_columns and drop_columns are cleared."""
    source = make_data_source(
        columns={("src_cat", "src_schema", "employees"): [schema_fixture_factory("emp_id", "int")]},
    )
    target = make_data_source(
        columns={("tgt_cat", "tgt_schema", "employees"): [schema_fixture_factory("emp_id", "int")]},
    )

    table = Table(
        source_name="employees",
        target_name="employees",
        select_columns=["stale_select"],
        drop_columns=["stale_drop"],
    )
    configured = ColumnMappingAutoConfigurer().configure(table, _ctx(source, target, table))

    assert configured.select_columns is None
    assert configured.drop_columns is None


def test_column_mapping_auto_configurer_overwrites_existing_fields(make_data_source):
    """All three managed fields are regenerated from the current matcher pass — stale entries discarded."""
    source = make_data_source(
        columns={
            ("src_cat", "src_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("emp-name", "string"),
            ],
        },
    )
    target = make_data_source(
        columns={
            ("tgt_cat", "tgt_schema", "employees"): [
                schema_fixture_factory("emp_id", "int"),
                schema_fixture_factory("emp_name", "string"),
            ],
        },
    )

    table = Table(
        source_name="employees",
        target_name="employees",
        column_mapping=[ColumnMapping(source_name="stale_src", target_name="stale_tgt")],
        select_columns=["stale_select"],
        drop_columns=["stale_drop"],
    )
    configured = ColumnMappingAutoConfigurer().configure(table, _ctx(source, target, table))

    assert configured.column_mapping == [ColumnMapping(source_name="emp-name", target_name="emp_name")]
    assert configured.select_columns is None
    assert configured.drop_columns is None
