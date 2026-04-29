from unittest.mock import MagicMock

from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions


def test_read_data_simple_query():
    spark = MagicMock()
    reader = RemoteQueryReader(spark, "my_connection")

    reader.read_data("SELECT * FROM employees", "my_db", "database")

    spark.sql.assert_called_once_with(
        "SELECT * FROM remote_query('my_connection', query => 'SELECT * FROM employees', database => 'my_db')"
    )


def test_read_data_with_service_name_catalog_key():
    spark = MagicMock()
    reader = RemoteQueryReader(spark, "oracle_conn")

    reader.read_data("SELECT 1 FROM dual", "ORCL", "service_name", "dbtable")

    spark.sql.assert_called_once_with(
        "SELECT * FROM remote_query('oracle_conn', dbtable => 'SELECT 1 FROM dual', serviceName => 'ORCL')"
    )


def test_read_data_with_options():
    spark = MagicMock()
    reader = RemoteQueryReader(spark, "tsql_conn")
    options = JdbcReaderOptions(
        num_partitions=10,
        partition_column="id",
        lower_bound="0",
        upper_bound="1000",
        fetchsize=500,
    )

    reader.read_data("SELECT * FROM orders", "my_db", "database", "dbtable", options)

    spark.sql.assert_called_once_with(
        "SELECT * FROM remote_query('tsql_conn', "
        "dbtable => 'SELECT * FROM orders', "
        "database => 'my_db', "
        "partitionColumn => 'id', "
        "numPartitions => '10', "
        "lowerBound => '0', "
        "upperBound => '1000', "
        "fetchsize => '500')"
    )


def test_read_data_escapes_single_quotes():
    spark = MagicMock()
    reader = RemoteQueryReader(spark, "my_conn")

    reader.read_data("SELECT * FROM t WHERE name = 'val'", "db", "database")

    spark.sql.assert_called_once_with(
        r"SELECT * FROM remote_query('my_conn', query => 'SELECT * FROM t WHERE name = \'val\'', database => 'db')"
    )


def test_read_data_without_options():
    spark = MagicMock()
    reader = RemoteQueryReader(spark, "sf_conn")

    reader.read_data("SELECT col1 FROM schema.table", "MY_DB", "database", "query")

    spark.sql.assert_called_once_with(
        "SELECT * FROM remote_query('sf_conn', query => 'SELECT col1 FROM schema.table', database => 'MY_DB')"
    )
