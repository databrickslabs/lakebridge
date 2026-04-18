import re
from unittest.mock import MagicMock, create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.databricks import (
    DatabricksSourceDataSource,
    DatabricksTargetDataSource,
)
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.sdk import WorkspaceClient


def initial_setup():
    pyspark_sql_session = MagicMock()
    spark = pyspark_sql_session.SparkSession.builder.getOrCreate()

    # Define the source, workspace, and scope
    engine = get_dialect("databricks")
    ws = create_autospec(WorkspaceClient)
    scope = "scope"
    return engine, spark, ws, scope


def test_get_schema_target():
    """Target uses information_schema with full_data_type for native UC catalogs."""
    engine, spark, ws, scope = initial_setup()
    ddds = DatabricksTargetDataSource(engine, spark, ws, scope)

    # catalog as catalog
    ddds.get_schema("catalog", "schema", "supplier")
    spark.sql.assert_called_with(
        re.sub(
            r'\s+',
            ' ',
            """select lower(column_name) as col_name, full_data_type as data_type from
                    catalog.information_schema.columns where lower(table_catalog)='catalog'
                    and lower(table_schema)='schema' and lower(table_name) ='supplier' order by
                    col_name""",
        )
    )
    spark.sql().selectExpr.assert_called_with("col_name as column_name", "data_type")
    spark.sql().selectExpr().where.assert_called_with("column_name not like '#%'")

    # hive_metastore as catalog
    ddds.get_schema("hive_metastore", "schema", "supplier")
    spark.sql.assert_called_with(re.sub(r'\s+', ' ', """describe table hive_metastore.schema.supplier"""))
    spark.sql().selectExpr.assert_called_with("col_name as column_name", "data_type")
    spark.sql().selectExpr().where.assert_called_with("column_name not like '#%'")

    # global_temp as schema with hive_metastore
    ddds.get_schema("hive_metastore", "global_temp", "supplier")
    spark.sql.assert_called_with(re.sub(r'\s+', ' ', """describe table global_temp.supplier"""))
    spark.sql().selectExpr.assert_called_with("col_name as column_name", "data_type")
    spark.sql().selectExpr().where.assert_called_with("column_name not like '#%'")


def test_get_schema_source():
    """Source always uses DESCRIBE TABLE, which works for hive, global_temp, and Foreign Catalogs."""
    engine, spark, ws, scope = initial_setup()
    ddds = DatabricksSourceDataSource(engine, spark, ws, scope)

    # UC catalog — source uses DESCRIBE TABLE (not information_schema)
    ddds.get_schema("catalog", "schema", "supplier")
    spark.sql.assert_called_with("describe table catalog.schema.supplier")

    # hive_metastore
    ddds.get_schema("hive_metastore", "schema", "supplier")
    spark.sql.assert_called_with("describe table hive_metastore.schema.supplier")

    # global_temp
    ddds.get_schema("hive_metastore", "global_temp", "supplier")
    spark.sql.assert_called_with("describe table global_temp.supplier")

    # Foreign Catalog
    ddds.get_schema("foreign_catalog", "public", "customers")
    spark.sql.assert_called_with("describe table foreign_catalog.public.customers")


def test_read_data_from_uc():
    # initial setup
    engine, spark, ws, scope = initial_setup()

    # read_data is inherited from DatabricksDataSource; test with source subclass
    ddds = DatabricksSourceDataSource(engine, spark, ws, scope)

    # Test with query
    ddds.read_data("org", "data", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from org.data.employee")

    # global_temp as schema with UC catalog
    ddds.read_data("org", "global_temp", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from global_temp.employee")


def test_read_data_from_hive():
    # initial setup
    engine, spark, ws, scope = initial_setup()

    ddds = DatabricksSourceDataSource(engine, spark, ws, scope)

    # Test with query
    ddds.read_data("hive_metastore", "data", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from hive_metastore.data.employee")

    # global_temp as schema with hive_metastore
    ddds.read_data("hive_metastore", "global_temp", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from global_temp.employee")


def test_read_data_exception_handling():
    # initial setup
    engine, spark, ws, scope = initial_setup()

    ddds = DatabricksSourceDataSource(engine, spark, ws, scope)
    spark.sql.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select id as id, ename as name from "
        "org.data.employee : Test Exception",
    ):
        ddds.read_data("org", "data", "employee", "select id as id, ename as name from :tbl", None)


def test_get_schema_target_exception_handling():
    """Target schema fetch exception includes the information_schema query in the error."""
    engine, spark, ws, scope = initial_setup()

    ddds = DatabricksTargetDataSource(engine, spark, ws, scope)
    spark.sql.side_effect = RuntimeError("Test Exception")
    with pytest.raises(DataSourceRuntimeException) as exception:
        ddds.get_schema("org", "data", "employee")

    assert str(exception.value) == (
        "Runtime exception occurred while fetching schema using select lower(column_name) "
        "as col_name, full_data_type as data_type from org.information_schema.columns "
        "where lower(table_catalog)='org' and lower(table_schema)='data' and lower("
        "table_name) ='employee' order by col_name : Test Exception"
    )


def test_get_schema_source_exception_handling():
    """Source schema fetch exception includes the DESCRIBE TABLE query in the error."""
    engine, spark, ws, scope = initial_setup()

    ddds = DatabricksSourceDataSource(engine, spark, ws, scope)
    spark.sql.side_effect = RuntimeError("Test Exception")
    with pytest.raises(DataSourceRuntimeException) as exception:
        ddds.get_schema("org", "data", "employee")

    assert "describe table org.data.employee" in str(exception.value)
    assert "Test Exception" in str(exception.value)


def test_get_schema_source_foreign_catalog():
    """Source correctly uses DESCRIBE TABLE for Foreign Catalogs without needing fallback."""
    engine, spark, ws, scope = initial_setup()
    ddds = DatabricksSourceDataSource(engine, spark, ws, scope)

    ddds.get_schema("foreign_catalog", "public", "customers")

    # Only one SQL call — no fallback needed since source always uses DESCRIBE TABLE
    assert spark.sql.call_count == 1
    spark.sql.assert_called_with("describe table foreign_catalog.public.customers")


def test_normalize_identifier():
    engine, spark, ws, scope = initial_setup()
    data_source = DatabricksSourceDataSource(engine, spark, ws, scope)

    assert data_source.normalize_identifier("a") == NormalizedIdentifier("`a`", '`a`')
    assert data_source.normalize_identifier('`b`') == NormalizedIdentifier("`b`", '`b`')
    assert data_source.normalize_identifier('e`f') == NormalizedIdentifier("`e``f`", '`e``f`')
    assert data_source.normalize_identifier('`e``f`') == NormalizedIdentifier("`e``f`", '`e``f`')
    assert data_source.normalize_identifier('` g h `') == NormalizedIdentifier("` g h `", '` g h `')
    assert data_source.normalize_identifier('`j"k`') == NormalizedIdentifier('`j"k`', '`j"k`')
