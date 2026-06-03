import re
from unittest.mock import create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.teradata import TeradataDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions


def initial_setup():
    engine = get_dialect("teradata")
    reader = create_autospec(RemoteQueryReader)
    return engine, reader


def test_read_data_with_options():
    engine, reader = initial_setup()

    tds = TeradataDataSource(engine, reader)
    options = JdbcReaderOptions(num_partitions=50, partition_column="emp_id", lower_bound="0", upper_bound="100")

    tds.read_data("REMORPH", "tpch", "employee", "select 1 from :tbl", options)

    reader.read_data.assert_called_once_with(
        "select 1 from tpch.employee",
        "REMORPH",
        "database",
        "query",
        options,
    )


def test_read_data_without_options():
    engine, reader = initial_setup()
    tds = TeradataDataSource(engine, reader)

    tds.read_data("REMORPH", "tpch", "employee", "select 1 from :tbl", None)

    reader.read_data.assert_called_once_with(
        "select 1 from tpch.employee",
        "REMORPH",
        "database",
        "query",
        None,
    )


def test_get_schema():
    engine, reader = initial_setup()
    tds = TeradataDataSource(engine, reader)

    tds.get_schema("REMORPH", "tpch", "employee")

    reader.read_data.assert_called_once()
    call_args = reader.read_data.call_args
    source_query = call_args[0][0]
    expected_query = re.sub(
        r'\s+',
        ' ',
        """SELECT
            TRIM(ColumnName) AS column_name,
            CASE
                WHEN ColumnType IN ('I')  THEN 'integer'
                WHEN ColumnType IN ('I1') THEN 'byteint'
                WHEN ColumnType IN ('I2') THEN 'smallint'
                WHEN ColumnType IN ('I8') THEN 'bigint'
                WHEN ColumnType IN ('D')  THEN
                    'decimal(' || TRIM(CAST(DecimalTotalDigits AS VARCHAR(10))) || ','
                    || TRIM(CAST(DecimalFractionalDigits AS VARCHAR(10))) || ')'
                WHEN ColumnType IN ('F')  THEN 'double precision'
                WHEN ColumnType IN ('CF') THEN 'char(' || TRIM(CAST(ColumnLength AS VARCHAR(10))) || ')'
                WHEN ColumnType IN ('CV') THEN 'varchar(' || TRIM(CAST(ColumnLength AS VARCHAR(10))) || ')'
                WHEN ColumnType IN ('DA') THEN 'date'
                WHEN ColumnType IN ('AT') THEN 'time'
                WHEN ColumnType IN ('TS') THEN 'timestamp'
                WHEN ColumnType IN ('TZ') THEN 'time with time zone'
                WHEN ColumnType IN ('SZ') THEN 'timestamp with time zone'
                WHEN ColumnType IN ('BO') THEN 'blob'
                WHEN ColumnType IN ('CO') THEN 'clob'
                WHEN ColumnType IN ('BF') THEN 'byte(' || TRIM(CAST(ColumnLength AS VARCHAR(10))) || ')'
                WHEN ColumnType IN ('BV') THEN 'varbyte(' || TRIM(CAST(ColumnLength AS VARCHAR(10))) || ')'
                WHEN ColumnType IN ('N')  THEN
                    'number(' || TRIM(CAST(DecimalTotalDigits AS VARCHAR(10))) || ','
                    || TRIM(CAST(DecimalFractionalDigits AS VARCHAR(10))) || ')'
                WHEN ColumnType IN ('JN') THEN 'json'
                WHEN ColumnType IN ('XM') THEN 'xml'
                ELSE LOWER(TRIM(ColumnType))
            END AS data_type
            FROM DBC.ColumnsV
            WHERE LOWER(TableName) = LOWER('employee')
            AND LOWER(DatabaseName) = LOWER('tpch')
      """,
    )
    assert source_query == expected_query
    assert call_args[0][1] == "REMORPH"
    assert call_args[0][2] == "database"
    assert call_args[0][3] == "query"


def test_read_data_exception_handling():
    engine, reader = initial_setup()
    tds = TeradataDataSource(engine, reader)

    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select 1 from tpch.employee : Test Exception",
    ):
        tds.read_data("REMORPH", "tpch", "employee", "select 1 from :tbl", None)


def test_get_schema_exception_handling():
    engine, reader = initial_setup()
    tds = TeradataDataSource(engine, reader)

    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching schema",
    ):
        tds.get_schema("REMORPH", "tpch", "employee")


def test_normalize_identifier():
    engine, reader = initial_setup()
    data_source = TeradataDataSource(engine, reader)

    assert data_source.normalize_identifier("a") == NormalizedIdentifier("`a`", '"a"')
    assert data_source.normalize_identifier('"b"') == NormalizedIdentifier("`b`", '"b"')
    assert data_source.normalize_identifier('"`e`f`"') == NormalizedIdentifier("```e``f```", '"`e`f`"')
    assert data_source.normalize_identifier('" g h "') == NormalizedIdentifier("` g h `", '" g h "')
    assert data_source.normalize_identifier('"""j""k"""') == NormalizedIdentifier('`"j"k"`', '"""j""k"""')
    assert data_source.normalize_identifier('"j""k"') == NormalizedIdentifier('`j"k`', '"j""k"')
