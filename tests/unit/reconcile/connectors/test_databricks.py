import re
from unittest.mock import MagicMock, create_autospec

import pytest

from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.databricks import DatabricksDataSource
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


def test_get_schema():
    # initial setup
    engine, spark, ws, scope = initial_setup()

    # catalog as catalog
    ddds = DatabricksDataSource(engine, spark, ws, scope)
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
    spark.sql().where.assert_called_with("col_name not like '#%'")

    # hive_metastore as catalog
    ddds.get_schema("hive_metastore", "schema", "supplier")
    spark.sql.assert_called_with(re.sub(r'\s+', ' ', """describe table hive_metastore.schema.supplier"""))
    spark.sql().where.assert_called_with("col_name not like '#%'")

    # global_temp as schema with hive_metastore
    ddds.get_schema("hive_metastore", "global_temp", "supplier")
    spark.sql.assert_called_with(re.sub(r'\s+', ' ', """describe table global_temp.supplier"""))
    spark.sql().where.assert_called_with("col_name not like '#%'")


def test_read_data_from_uc():
    # initial setup
    engine, spark, ws, scope = initial_setup()

    # create object for DatabricksDataSource
    ddds = DatabricksDataSource(engine, spark, ws, scope)

    # Test with query
    ddds.read_data("org", "data", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from org.data.employee")

    # global_temp as schema with UC catalog
    ddds.read_data("org", "global_temp", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from global_temp.employee")


def test_read_data_from_hive():
    # initial setup
    engine, spark, ws, scope = initial_setup()

    # create object for DatabricksDataSource
    ddds = DatabricksDataSource(engine, spark, ws, scope)

    # Test with query
    ddds.read_data("hive_metastore", "data", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from hive_metastore.data.employee")

    # global_temp as schema with hive_metastore
    ddds.read_data("hive_metastore", "global_temp", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from global_temp.employee")


def test_read_data_exception_handling():
    # initial setup
    engine, spark, ws, scope = initial_setup()

    # create object for DatabricksDataSource
    ddds = DatabricksDataSource(engine, spark, ws, scope)
    spark.sql.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select id as id, ename as name from "
        "org.data.employee : Test Exception",
    ):
        ddds.read_data("org", "data", "employee", "select id as id, ename as name from :tbl", None)


def test_get_schema_exception_handling():
    # initial setup
    engine, spark, ws, scope = initial_setup()

    # create object for DatabricksDataSource
    ddds = DatabricksDataSource(engine, spark, ws, scope)
    spark.sql.side_effect = RuntimeError("Test Exception")
    with pytest.raises(DataSourceRuntimeException) as exception:
        ddds.get_schema("org", "data", "employee")

    assert str(exception.value) == (
        "Runtime exception occurred while fetching schema using select lower(column_name) "
        "as col_name, full_data_type as data_type from org.information_schema.columns "
        "where lower(table_catalog)='org' and lower(table_schema)='data' and lower("
        "table_name) ='employee' order by col_name : Test Exception"
    )
