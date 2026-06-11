import re
from unittest.mock import MagicMock

import pytest

from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.databricks import (
    DatabricksDataSource,
    DatabricksNonUnityCatalogDataSource,
)
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException


def initial_setup():
    pyspark_sql_session = MagicMock()
    spark = pyspark_sql_session.SparkSession.builder.getOrCreate()

    engine = get_dialect("databricks")
    return engine, spark


def test_get_schema_uses_information_schema():
    """DatabricksDataSource always uses information_schema (UC native catalogs only)."""
    engine, spark = initial_setup()
    ddds = DatabricksDataSource(engine, spark)

    ddds.get_schema("catalog", "schema", "supplier")
    spark.sql.assert_called_with(
        re.sub(
            r'\s+',
            ' ',
            """select lower(column_name) as col_name, full_data_type as data_type from
                    catalog.information_schema.columns where lower(table_schema)=lower('schema')
                    and lower(table_name) =lower('supplier') order by col_name""",
        )
    )
    spark.sql().selectExpr.assert_called_with("col_name as column_name", "data_type")
    spark.sql().selectExpr().where.assert_called_with("column_name not like '#%'")


def test_get_schema_non_uc_uses_describe_table():
    """DatabricksNonUnityCatalogDataSource always uses DESCRIBE TABLE for hive, global_temp, and Foreign Catalogs."""
    engine, spark = initial_setup()
    ddds = DatabricksNonUnityCatalogDataSource(engine, spark)

    # UC catalog — non-UC variant still uses DESCRIBE TABLE (caller chooses the variant)
    ddds.get_schema("catalog", "schema", "supplier")
    spark.sql.assert_called_with("describe table catalog.schema.supplier")

    # hive_metastore
    ddds.get_schema("hive_metastore", "schema", "supplier")
    spark.sql.assert_called_with("describe table hive_metastore.schema.supplier")

    # global_temp
    ddds.get_schema("hive_metastore", "global_temp", "supplier")
    spark.sql.assert_called_with("describe table global_temp.supplier")

    # Foreign Catalog (Lakehouse Federation)
    ddds.get_schema("foreign_catalog", "public", "customers")
    spark.sql.assert_called_with("describe table foreign_catalog.public.customers")


def test_read_data_from_uc():
    engine, spark = initial_setup()

    ddds = DatabricksDataSource(engine, spark)

    # Test with query
    ddds.read_data("org", "data", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from org.data.employee")

    # global_temp as schema with UC catalog
    ddds.read_data("org", "global_temp", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from global_temp.employee")


def test_read_data_from_hive():
    engine, spark = initial_setup()

    ddds = DatabricksNonUnityCatalogDataSource(engine, spark)

    # Test with query
    ddds.read_data("hive_metastore", "data", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from hive_metastore.data.employee")

    # global_temp as schema with hive_metastore
    ddds.read_data("hive_metastore", "global_temp", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from global_temp.employee")


def test_read_data_exception_handling():
    engine, spark = initial_setup()

    ddds = DatabricksDataSource(engine, spark)
    spark.sql.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select id as id, ename as name from "
        "org.data.employee : Test Exception",
    ):
        ddds.read_data("org", "data", "employee", "select id as id, ename as name from :tbl", None)


def test_get_schema_information_schema_exception_handling():
    """DatabricksDataSource schema fetch exception includes the information_schema query."""
    engine, spark = initial_setup()

    ddds = DatabricksDataSource(engine, spark)
    spark.sql.side_effect = RuntimeError("Test Exception")
    with pytest.raises(DataSourceRuntimeException):
        ddds.get_schema("org", "data", "employee")


def test_get_schema_describe_exception_handling():
    """DatabricksNonUnityCatalogDataSource schema fetch exception includes the DESCRIBE TABLE query."""
    engine, spark = initial_setup()

    ddds = DatabricksNonUnityCatalogDataSource(engine, spark)
    spark.sql.side_effect = RuntimeError("Test Exception")
    with pytest.raises(DataSourceRuntimeException) as exception:
        ddds.get_schema("org", "data", "employee")

    assert "describe table org.data.employee" in str(exception.value)
    assert "Test Exception" in str(exception.value)


def test_get_schema_non_uc_foreign_catalog():
    """DatabricksNonUnityCatalogDataSource uses DESCRIBE TABLE for Foreign Catalogs without fallback."""
    engine, spark = initial_setup()
    ddds = DatabricksNonUnityCatalogDataSource(engine, spark)

    ddds.get_schema("foreign_catalog", "public", "customers")

    # Only one SQL call — no fallback needed since the non-UC variant always uses DESCRIBE TABLE
    assert spark.sql.call_count == 1
    spark.sql.assert_called_with("describe table foreign_catalog.public.customers")


def test_normalize_identifier():
    engine, spark = initial_setup()
    data_source = DatabricksDataSource(engine, spark)

    assert data_source.normalize_identifier("a") == NormalizedIdentifier("`a`", '`a`')
    assert data_source.normalize_identifier('`b`') == NormalizedIdentifier("`b`", '`b`')
    assert data_source.normalize_identifier('e`f') == NormalizedIdentifier("`e``f`", '`e``f`')
    assert data_source.normalize_identifier('`e``f`') == NormalizedIdentifier("`e``f`", '`e``f`')
    assert data_source.normalize_identifier('` g h `') == NormalizedIdentifier("` g h `", '` g h `')
    assert data_source.normalize_identifier('`j"k`') == NormalizedIdentifier('`j"k`', '`j"k`')
