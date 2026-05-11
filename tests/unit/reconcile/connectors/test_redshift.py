import re
from unittest.mock import create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.redshift import RedshiftDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions


def initial_setup():
    engine = get_dialect("redshift")
    reader = create_autospec(RemoteQueryReader)
    return engine, reader


def test_read_data_with_options():
    engine, reader = initial_setup()

    rds = RedshiftDataSource(engine, reader)
    options = JdbcReaderOptions(num_partitions=50, partition_column="s_nationkey", lower_bound="0", upper_bound="100")

    rds.read_data("dev", "data", "employee", "select 1 from :tbl", options)

    reader.read_data.assert_called_once_with(
        "select 1 from data.employee",
        "dev",
        "database",
        "query",
        options,
    )


def test_read_data_replaces_postgres_param_syntax():
    """SQLGlot's Redshift dialect emits :tbl as %(tbl)s; the connector must handle both."""
    engine, reader = initial_setup()
    rds = RedshiftDataSource(engine, reader)

    rds.read_data("dev", "data", "employee", "select 1 from %(tbl)s", None)

    reader.read_data.assert_called_once_with(
        "select 1 from data.employee",
        "dev",
        "database",
        "query",
        None,
    )


def test_get_schema():
    engine, reader = initial_setup()
    rds = RedshiftDataSource(engine, reader)

    rds.get_schema("dev", "data", "employee")

    reader.read_data.assert_called_once()
    call_args = reader.read_data.call_args
    source_query = call_args[0][0]
    expected_query = re.sub(
        r'\s+',
        ' ',
        """SELECT
             column_name,
             CASE
                WHEN data_type = 'numeric' AND numeric_precision IS NOT NULL
                    THEN 'decimal(' || numeric_precision || ',' || numeric_scale || ')'
                WHEN data_type = 'character varying' AND character_maximum_length IS NOT NULL
                    THEN 'varchar(' || character_maximum_length || ')'
                WHEN data_type = 'character' AND character_maximum_length IS NOT NULL
                    THEN 'char(' || character_maximum_length || ')'
                WHEN data_type IN ('binary varying')
                    THEN 'binary'
                ELSE data_type
            END AS data_type
            FROM
                information_schema.columns
            WHERE
            LOWER(table_name) = LOWER('employee')
            AND LOWER(table_schema) = LOWER('data')
            ORDER BY ordinal_position
      """,
    )
    assert source_query == expected_query
    assert call_args[0][1] == "dev"
    assert call_args[0][2] == "database"
    assert call_args[0][3] == "query"


def test_read_data_exception_handling():
    engine, reader = initial_setup()
    rds = RedshiftDataSource(engine, reader)

    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select 1 from data.employee : Test Exception",
    ):
        rds.read_data("dev", "data", "employee", "select 1 from :tbl", None)


def test_get_schema_exception_handling():
    engine, reader = initial_setup()
    rds = RedshiftDataSource(engine, reader)

    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching schema",
    ):
        rds.get_schema("dev", "data", "employee")


def test_normalize_identifier():
    engine, reader = initial_setup()
    data_source = RedshiftDataSource(engine, reader)

    assert data_source.normalize_identifier("a") == NormalizedIdentifier("`a`", '"a"')
    assert data_source.normalize_identifier('"b"') == NormalizedIdentifier("`b`", '"b"')
    assert data_source.normalize_identifier('"`e`f`"') == NormalizedIdentifier("```e``f```", '"`e`f`"')
    assert data_source.normalize_identifier('" g h "') == NormalizedIdentifier("` g h `", '" g h "')
    assert data_source.normalize_identifier('"""j""k"""') == NormalizedIdentifier('`"j"k"`', '"""j""k"""')
    assert data_source.normalize_identifier('"j""k"') == NormalizedIdentifier('`j"k`', '"j""k"')
