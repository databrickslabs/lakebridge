from unittest.mock import MagicMock

from pyspark.sql import DataFrame, Row
from pyspark.sql.types import DataType, IntegerType, StructField, StructType

from databricks.labs.lakebridge.reconcile.query_builder.sampling_query import SamplingQueryBuilder
from databricks.labs.lakebridge.reconcile.recon_config import Filters
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from tests.conftest import ansi_schema_fixture_factory, oracle_schema_fixture_factory


def test_build_query_with_alias_emits_select_with_filter(table_conf, fake_databricks_datasource):
    builder = SamplingQueryBuilder(
        table_conf(
            join_columns=["`s_suppkey`"],
            select_columns=["`s_address`", "`s_name`"],
            filters=Filters(source="s_suppkey = 1"),
        ),
        [
            ansi_schema_fixture_factory("s_suppkey", "bigint"),
            ansi_schema_fixture_factory("s_address", "string"),
            ansi_schema_fixture_factory("s_name", "string"),
        ],
        "source",
        get_dialect("databricks"),
        fake_databricks_datasource,
    )

    sql = builder.build_query_with_alias()

    assert sql == (
        "SELECT COALESCE(TRIM(`s_address`), '_null_recon_') AS `s_address`, "
        "COALESCE(TRIM(`s_name`), '_null_recon_') AS `s_name`, "
        "COALESCE(TRIM(`s_suppkey`), '_null_recon_') AS `s_suppkey` "
        "FROM :tbl WHERE COALESCE(TRIM(`s_suppkey`), '_null_recon_') = 1"
    )


def test_build_query_databricks_engine_registers_temp_view(table_conf, fake_databricks_datasource):
    df = MagicMock(spec=DataFrame)
    keys_df = MagicMock(spec=DataFrame)
    df.select.return_value = keys_df

    builder = SamplingQueryBuilder(
        table_conf(join_columns=["`s_suppkey`"]),
        [ansi_schema_fixture_factory("s_suppkey", "bigint")],
        "source",
        get_dialect("databricks"),
        fake_databricks_datasource,
    )

    sql = builder.build_query(df)

    keys_df.createOrReplaceTempView.assert_called_once()
    view_name = keys_df.createOrReplaceTempView.call_args.args[0]
    assert view_name.startswith("recon_keys_")
    assert f"FROM {view_name}" in sql


def _stub_keys_df(rows: list[Row], column_types: list[tuple[str, type[DataType]]]) -> MagicMock:
    keys_df = MagicMock(spec=DataFrame)
    keys_df.columns = [name for name, _ in column_types]
    keys_df.schema = StructType([StructField(name, dtype()) for name, dtype in column_types])
    keys_df.collect.return_value = rows
    return keys_df


def test_build_query_non_databricks_engine_emits_union_recon_subquery(table_conf, fake_oracle_datasource):
    df = MagicMock(spec=DataFrame)
    keys_df = _stub_keys_df(
        rows=[Row(s_nationkey=11, s_suppkey=1), Row(s_nationkey=22, s_suppkey=2)],
        column_types=[("s_nationkey", IntegerType), ("s_suppkey", IntegerType)],
    )
    df.select.return_value = keys_df

    builder = SamplingQueryBuilder(
        table_conf(join_columns=["`s_suppkey`", "`s_nationkey`"]),
        [
            oracle_schema_fixture_factory("s_suppkey", "number"),
            oracle_schema_fixture_factory("s_nationkey", "number"),
        ],
        "source",
        get_dialect("snowflake"),
        fake_oracle_datasource,
    )

    sql = builder.build_query(df)

    keys_df.createOrReplaceTempView.assert_not_called()
    assert "INNER JOIN (SELECT" in sql and ") AS recon" in sql
    assert sql.count("UNION") == 1  # 2 rows → 1 UNION
    assert "11" in sql and "22" in sql


def test_build_query_oracle_engine_uses_dual_in_union_recon_cte(table_conf, fake_oracle_datasource):
    df = MagicMock(spec=DataFrame)
    keys_df = _stub_keys_df(
        rows=[Row(s_nationkey=11, s_suppkey=1)],
        column_types=[("s_nationkey", IntegerType), ("s_suppkey", IntegerType)],
    )
    df.select.return_value = keys_df

    builder = SamplingQueryBuilder(
        table_conf(join_columns=["`s_suppkey`", "`s_nationkey`"]),
        [
            oracle_schema_fixture_factory("s_suppkey", "number"),
            oracle_schema_fixture_factory("s_nationkey", "number"),
        ],
        "source",
        get_dialect("oracle"),
        fake_oracle_datasource,
    )

    sql = builder.build_query(df)

    keys_df.createOrReplaceTempView.assert_not_called()
    assert "FROM dual" in sql  # Oracle requires FROM dual for bare SELECT
