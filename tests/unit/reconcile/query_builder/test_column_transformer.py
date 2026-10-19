import pytest

from databricks.labs.lakebridge.reconcile.connectors.data_source import MockDataSource
from databricks.labs.lakebridge.reconcile.query_builder.column_transformer import (
    ReconcileLayer,
    RuleBasedColumnTransformer,
)
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import build_column_no_alias
from databricks.labs.lakebridge.reconcile.recon_config import ColumnMapping, Table
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from tests.conftest import ansi_schema_fixture_factory

UNIVERSAL_SQL = "COALESCE(TRIM(c), '_null_recon_')"


def _transformer(source: str, source_type: str, target: str, target_type: str, conf: Table | None = None):
    data_source = MockDataSource({}, {})
    return RuleBasedColumnTransformer(
        source=ReconcileLayer(data_source, get_dialect(source), [ansi_schema_fixture_factory("c", source_type)]),
        target=ReconcileLayer(data_source, get_dialect(target), [ansi_schema_fixture_factory("c", target_type)]),
        table_conf=conf or Table(source_name="t", target_name="t"),
    )


def _render(transformer: RuleBasedColumnTransformer, layer: str, dialect: str, column: str = "c") -> str:
    return transformer.transform([build_column_no_alias(this=column)], layer)[0].column.sql(dialect=dialect)


@pytest.mark.parametrize("target_type", ["timestamp", "timestamptz"])
@pytest.mark.parametrize(
    "source_type, expected_source",
    [
        ("timestamp", "COALESCE(TO_CHAR(c, 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')"),
        ("timestamptz", "COALESCE(TO_CHAR(c AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')"),
    ],
)
def test_redshift_to_databricks_timestamps_are_aligned(source_type, expected_source, target_type):
    transformer = _transformer("redshift", source_type, "databricks", target_type)
    assert _render(transformer, "source", "redshift") == expected_source
    assert (
        _render(transformer, "target", "databricks")
        == "COALESCE(DATE_FORMAT(c, 'yyyy-MM-dd HH:mm:ss.SSSSSS'), '_null_recon_')"
    )


def test_redshift_boolean_is_serialized_as_text():
    transformer = _transformer("redshift", "boolean", "databricks", "boolean")
    assert _render(transformer, "source", "redshift") == (
        "COALESCE(CASE WHEN c THEN 'true' WHEN NOT c THEN 'false' ELSE NULL END, '_null_recon_')"
    )
    assert _render(transformer, "target", "databricks") == UNIVERSAL_SQL


@pytest.mark.parametrize("source_type", ["double", "double precision", "real", "float4"])
@pytest.mark.parametrize("target_type", ["double", "float"])
def test_redshift_to_databricks_floats_are_pinned_to_fixed_scale(source_type, target_type):
    transformer = _transformer("redshift", source_type, "databricks", target_type)
    source_sql = _render(transformer, "source", "redshift")
    target_sql = _render(transformer, "target", "databricks")

    assert "CAST(c AS VARCHAR) IN ('NaN', 'Infinity', '-Infinity')" in source_sql
    assert "CAST(CAST(c AS DECIMAL(38,10)) AS VARCHAR)" in source_sql
    assert "ISNAN(c)" in target_sql
    assert "CAST(CAST(c AS DECIMAL(38,10)) AS STRING)" in target_sql
    assert ">= 1e28" in source_sql
    assert ">= 1e28" in target_sql


@pytest.mark.parametrize(
    "source_type, target_type",
    [("double", "decimal(18,2)"), ("real", "int")],
)
def test_redshift_to_databricks_unpaired_types_use_default_serialization(source_type, target_type):
    transformer = _transformer("redshift", source_type, "databricks", target_type)
    assert _render(transformer, "source", "redshift") == UNIVERSAL_SQL
    assert _render(transformer, "target", "databricks") == UNIVERSAL_SQL


@pytest.mark.parametrize("source", ["snowflake", "oracle", "tsql", "bigquery"])
@pytest.mark.parametrize("datatype", ["double", "timestamp"])
def test_other_sources_to_databricks_are_unchanged(source, datatype):
    transformer = _transformer(source, datatype, "databricks", datatype)
    assert "DECIMAL(38,10)" not in _render(transformer, "target", "databricks")
    assert "DATE_FORMAT" not in _render(transformer, "target", "databricks")


def test_pair_is_resolved_through_the_column_mapping():
    data_source = MockDataSource({}, {})
    transformer = RuleBasedColumnTransformer(
        source=ReconcileLayer(data_source, get_dialect("redshift"), [ansi_schema_fixture_factory("a", "double")]),
        target=ReconcileLayer(data_source, get_dialect("databricks"), [ansi_schema_fixture_factory("b", "double")]),
        table_conf=Table(source_name="t", target_name="t", column_mapping=[ColumnMapping("`a`", "`b`")]),
    )
    assert "DECIMAL(38,10)" in _render(transformer, "source", "redshift", "a")
    assert "DECIMAL(38,10)" in _render(transformer, "target", "databricks", "b")


def test_transforms_without_a_counterpart_type_fall_back_to_defaults():
    transformer = _transformer("redshift", "double", "databricks", "double")
    pair = transformer.transforms(get_dialect("redshift"), "double", None)
    assert pair.source == pair.target
