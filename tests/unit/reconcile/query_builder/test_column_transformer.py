import pytest
from sqlglot import Dialect

from databricks.labs.lakebridge.reconcile.connectors.data_source import MockDataSource
from databricks.labs.lakebridge.reconcile.query_builder.column_transformer import (
    ColumnPair,
    ReconcileLayer,
    RuleBasedColumnTransformer,
    TransformPair,
)
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import build_column_no_alias
from databricks.labs.lakebridge.reconcile.recon_config import Table, Transformation
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from tests.conftest import ansi_schema_fixture_factory

UNIVERSAL_SQL = "COALESCE(TRIM(c), '_null_recon_')"
REDSHIFT_FLOAT_SQL = (
    "COALESCE(CASE WHEN CAST(c AS VARCHAR) IN ('NaN', 'Infinity', '-Infinity') OR ABS(c) >= 1e28 "
    "THEN CAST(c AS VARCHAR) ELSE CAST(CAST(c AS DECIMAL(38,10)) AS VARCHAR) END, '_null_recon_')"
)
DATABRICKS_FLOAT_SQL = (
    "COALESCE(CASE WHEN ISNAN(c) OR c IN (CAST('Infinity' AS DOUBLE), CAST('-Infinity' AS DOUBLE)) "
    "OR ABS(c) >= 1e28 THEN CAST(c AS STRING) "
    "ELSE REPLACE(FORMAT_NUMBER(CAST(c AS DECIMAL(38,10)), 10), ',', '') END, '_null_recon_')"
)
DATABRICKS_TIMESTAMP_SQL = "COALESCE(DATE_FORMAT(c, 'yyyy-MM-dd HH:mm:ss.SSSSSS'), '_null_recon_')"


def _transform(
    source: str | Dialect,
    source_type: str,
    target_type: str,
    conf: Table | None = None,
    target_column: str = "c",
) -> TransformPair:
    data_source = MockDataSource({}, {})
    source_dialect = get_dialect(source) if isinstance(source, str) else source
    transformer = RuleBasedColumnTransformer(
        source=ReconcileLayer(data_source, source_dialect, [ansi_schema_fixture_factory("c", source_type)]),
        target=ReconcileLayer(
            data_source, get_dialect("databricks"), [ansi_schema_fixture_factory(target_column, target_type)]
        ),
        table_conf=conf or Table(source_name="t", target_name="t"),
    )
    pair = ColumnPair(build_column_no_alias(this="c"), build_column_no_alias(this=target_column))
    return transformer.transform([pair])[0]


def _render(pair: TransformPair, source: str | Dialect = "redshift") -> tuple[str, str]:
    return pair.source.column.sql(dialect=source), pair.target.column.sql(dialect="databricks")


@pytest.mark.parametrize("target_type", ["timestamp", "timestamptz"])
@pytest.mark.parametrize(
    "source_type, expected_source",
    [
        ("timestamp", "COALESCE(TO_CHAR(c, 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')"),
        ("timestamp without time zone", "COALESCE(TO_CHAR(c, 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')"),
        ("timestamptz", "COALESCE(TO_CHAR(c AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')"),
        (
            "timestamp with time zone",
            "COALESCE(TO_CHAR(c AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')",
        ),
    ],
)
def test_redshift_to_databricks_timestamps_are_aligned(source_type, expected_source, target_type):
    assert _render(_transform("redshift", source_type, target_type)) == (expected_source, DATABRICKS_TIMESTAMP_SQL)


def test_redshift_boolean_is_serialized_as_text():
    assert _render(_transform("redshift", "boolean", "boolean")) == (
        "COALESCE(CASE WHEN c THEN 'true' WHEN NOT c THEN 'false' ELSE NULL END, '_null_recon_')",
        UNIVERSAL_SQL,
    )


@pytest.mark.parametrize("source_type", ["double", "double precision", "float8", "real", "float4"])
@pytest.mark.parametrize("target_type", ["double", "float"])
def test_redshift_to_databricks_floats_are_pinned_to_fixed_scale(source_type, target_type):
    assert _render(_transform("redshift", source_type, target_type)) == (REDSHIFT_FLOAT_SQL, DATABRICKS_FLOAT_SQL)


@pytest.mark.parametrize(
    "source_type, target_type",
    [("double", "decimal(18,2)"), ("real", "int"), ("timestamp", "string"), ("timestamp", "timestamp_ntz")],
)
def test_redshift_to_databricks_unpaired_types_use_default_serialization(source_type, target_type):
    source_sql, target_sql = _render(_transform("redshift", source_type, target_type))
    assert "DECIMAL(38,10)" not in source_sql
    assert "AT TIME ZONE" not in source_sql
    assert target_sql == UNIVERSAL_SQL


@pytest.mark.parametrize("source", ["snowflake", "oracle", "tsql", "bigquery"])
@pytest.mark.parametrize("datatype", ["double", "timestamp", "boolean"])
def test_other_sources_to_databricks_are_unchanged(source, datatype):
    source_sql, target_sql = _render(_transform(source, datatype, datatype), source)
    assert "DECIMAL(38,10)" not in source_sql
    assert "THEN 'true'" not in source_sql
    assert target_sql == UNIVERSAL_SQL


def test_differently_named_columns_are_transformed_as_a_pair():
    assert _render(_transform("redshift", "double", "double", target_column="d")) == (
        REDSHIFT_FLOAT_SQL,
        DATABRICKS_FLOAT_SQL.replace("(c", "(d").replace(" c ", " d "),
    )


def test_pair_carries_each_side_declared_type():
    pair = _transform("redshift", "double precision", "double")
    assert (pair.source.ansi_name, pair.source.original_type) == ("`c`", "double precision")
    assert (pair.target.ansi_name, pair.target.original_type) == ("`c`", "double")


def test_user_transformation_replaces_the_pair_transforms():
    conf = Table(
        source_name="t",
        target_name="t",
        transformations=[Transformation(column_name="`c`", source="ROUND(c, 2)", target="ROUND(c, 2)")],
    )
    pair = _transform("redshift", "double", "double", conf)
    assert _render(pair) == ("ROUND(c, 2)", "ROUND(c, 2)")
    assert pair.source.original_type is None
    assert pair.target.original_type is None


def test_missing_counterpart_falls_back_to_each_side_default():
    data_source = MockDataSource({}, {})
    transformer = RuleBasedColumnTransformer(
        source=ReconcileLayer(data_source, get_dialect("redshift"), [ansi_schema_fixture_factory("c", "double")]),
        target=ReconcileLayer(data_source, get_dialect("databricks"), [ansi_schema_fixture_factory("d", "double")]),
        table_conf=Table(source_name="t", target_name="t"),
    )
    pair = transformer.transform([ColumnPair(build_column_no_alias(this="c"), build_column_no_alias(this="c"))])[0]
    assert _render(pair) == (UNIVERSAL_SQL, "c")
    assert pair.source.original_type == "double"
    assert pair.target.original_type is None


def test_unparseable_type_falls_back_to_default_serialization():
    assert _render(_transform("redshift", "not a (type", "double")) == (UNIVERSAL_SQL, UNIVERSAL_SQL)


def test_unregistered_dialect_falls_back_to_universal_default():
    duckdb = Dialect.get_or_raise("duckdb")
    assert _render(_transform(duckdb, "double", "double"), duckdb) == (UNIVERSAL_SQL, UNIVERSAL_SQL)


@pytest.mark.parametrize("layer, expected", [("source", REDSHIFT_FLOAT_SQL), ("target", DATABRICKS_FLOAT_SQL)])
def test_for_layer_selects_the_layer_column(layer, expected):
    pair = _transform("redshift", "double", "double")
    assert pair.for_layer(layer).column.sql(dialect="redshift" if layer == "source" else "databricks") == expected
