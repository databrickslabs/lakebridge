import pytest
from pyspark import Row
from pyspark.testing import assertDataFrameEqual

from databricks.labs.lakebridge.reconcile.connectors.data_source import MockDataSource
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.recon_config import Schema

CATALOG = "org"
SCHEMA = "data"
TABLE = "employee"


def test_mock_data_source_happy(mock_spark):
    dataframe_repository = {
        (
            "org",
            "data",
            "select * from employee",
        ): mock_spark.createDataFrame(
            [
                Row(emp_id="1", emp_name="name-1", sal=100),
                Row(emp_id="2", emp_name="name-2", sal=200),
                Row(emp_id="3", emp_name="name-3", sal=300),
            ]
        )
    }
    schema_repository = {
        (CATALOG, SCHEMA, TABLE): [
            Schema(column_name="emp_id", data_type="int"),
            Schema(column_name="emp_name", data_type="str"),
            Schema(column_name="sal", data_type="int"),
        ]
    }

    data_source = MockDataSource(dataframe_repository, schema_repository)

    actual_data = data_source.read_data(CATALOG, SCHEMA, TABLE, "select * from employee", None)
    expected_data = mock_spark.createDataFrame(
        [
            Row(emp_id="1", emp_name="name-1", sal=100),
            Row(emp_id="2", emp_name="name-2", sal=200),
            Row(emp_id="3", emp_name="name-3", sal=300),
        ]
    )

    actual_schema = data_source.get_schema(CATALOG, SCHEMA, TABLE)
    assertDataFrameEqual(actual_data, expected_data)
    assert actual_schema == [
        Schema(column_name="emp_id", data_type="int"),
        Schema(column_name="emp_name", data_type="str"),
        Schema(column_name="sal", data_type="int"),
    ]


def test_mock_data_source_fail(mock_spark):
    data_source = MockDataSource({}, {}, Exception("TABLE NOT FOUND"))
    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using \\(org, data, select \\* from test\\) : TABLE"
        " NOT FOUND",
    ):
        data_source.read_data(CATALOG, SCHEMA, TABLE, "select * from test", None)

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching schema using \\(org, data, unknown\\) : TABLE NOT FOUND",
    ):
        data_source.get_schema(CATALOG, SCHEMA, "unknown")


def test_mock_data_source_no_catalog(mock_spark):
    dataframe_repository = {
        (
            "",
            "data",
            "select * from employee",
        ): mock_spark.createDataFrame(
            [
                Row(emp_id="1", emp_name="name-1", sal=100),
                Row(emp_id="2", emp_name="name-2", sal=200),
                Row(emp_id="3", emp_name="name-3", sal=300),
            ]
        )
    }
    schema_repository = {
        (CATALOG, SCHEMA, TABLE): [
            Schema(column_name="emp_id", data_type="int"),
            Schema(column_name="emp_name", data_type="str"),
            Schema(column_name="sal", data_type="int"),
        ]
    }

    data_source = MockDataSource(dataframe_repository, schema_repository)

    actual_data = data_source.read_data(None, SCHEMA, TABLE, "select * from employee", None)
    expected_data = mock_spark.createDataFrame(
        [
            Row(emp_id="1", emp_name="name-1", sal=100),
            Row(emp_id="2", emp_name="name-2", sal=200),
            Row(emp_id="3", emp_name="name-3", sal=300),
        ]
    )

    assertDataFrameEqual(actual_data, expected_data)
