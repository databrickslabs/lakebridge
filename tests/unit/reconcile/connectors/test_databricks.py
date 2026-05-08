from unittest.mock import MagicMock, create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
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
    return engine, spark, ws


def test_read_data_from_uc():
    # initial setup
    engine, spark, ws = initial_setup()

    # create object for DatabricksDataSource
    ddds = DatabricksDataSource(engine, spark, ws)

    # Test with query
    ddds.read_data("org", "data", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from org.data.employee")

    # global_temp as schema with UC catalog
    ddds.read_data("org", "global_temp", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from global_temp.employee")


def test_read_data_from_hive():
    # initial setup
    engine, spark, ws = initial_setup()

    # create object for DatabricksDataSource
    ddds = DatabricksDataSource(engine, spark, ws)

    # Test with query
    ddds.read_data("hive_metastore", "data", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from hive_metastore.data.employee")

    # global_temp as schema with hive_metastore
    ddds.read_data("hive_metastore", "global_temp", "employee", "select id as id, name as name from :tbl", None)
    spark.sql.assert_called_with("select id as id, name as name from global_temp.employee")


def test_read_data_exception_handling():
    # initial setup
    engine, spark, ws = initial_setup()

    # create object for DatabricksDataSource
    ddds = DatabricksDataSource(engine, spark, ws)
    spark.sql.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select id as id, ename as name from "
        "org.data.employee : Test Exception",
    ):
        ddds.read_data("org", "data", "employee", "select id as id, ename as name from :tbl", None)


def test_get_schema_exception_handling():
    # initial setup
    engine, spark, ws = initial_setup()

    # create object for DatabricksDataSource
    ddds = DatabricksDataSource(engine, spark, ws)
    spark.sql.side_effect = RuntimeError("Test Exception")
    with pytest.raises(DataSourceRuntimeException):
        ddds.get_schema("org", "data", "employee")


def test_normalize_identifier():
    engine, spark, ws = initial_setup()
    data_source = DatabricksDataSource(engine, spark, ws)

    assert data_source.normalize_identifier("a") == NormalizedIdentifier("`a`", '`a`')
    assert data_source.normalize_identifier('`b`') == NormalizedIdentifier("`b`", '`b`')
    assert data_source.normalize_identifier('e`f') == NormalizedIdentifier("`e``f`", '`e``f`')
    assert data_source.normalize_identifier('`e``f`') == NormalizedIdentifier("`e``f`", '`e``f`')
    assert data_source.normalize_identifier('` g h `') == NormalizedIdentifier("` g h `", '` g h `')
    assert data_source.normalize_identifier('`j"k`') == NormalizedIdentifier('`j"k`', '`j"k`')
