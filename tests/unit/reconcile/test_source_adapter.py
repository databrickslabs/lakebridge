from unittest.mock import create_autospec

import pytest

from databricks.connect import DatabricksSession
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.databricks import DatabricksDataSource
from databricks.labs.lakebridge.reconcile.connectors.oracle import OracleDataSource
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.connectors.source_adapter import create_adapter
from databricks.sdk import WorkspaceClient


def test_create_adapter_for_snowflake_dialect():
    spark = create_autospec(DatabricksSession)
    engine = get_dialect("snowflake")
    ws = create_autospec(WorkspaceClient)
    scope = "scope"

    data_source = create_adapter(engine, spark, ws, scope)

    assert isinstance(data_source, SnowflakeDataSource)


def test_create_adapter_for_oracle_dialect():
    spark = create_autospec(DatabricksSession)
    engine = get_dialect("oracle")
    ws = create_autospec(WorkspaceClient)
    scope = "scope"

    data_source = create_adapter(engine, spark, ws, scope)

    assert isinstance(data_source, OracleDataSource)


def test_create_adapter_for_databricks_dialect():
    spark = create_autospec(DatabricksSession)
    engine = get_dialect("databricks")
    ws = create_autospec(WorkspaceClient)
    scope = "scope"

    data_source = create_adapter(engine, spark, ws, scope)

    assert isinstance(data_source, DatabricksDataSource)


def test_raise_exception_for_unknown_dialect():
    spark = create_autospec(DatabricksSession)
    engine = get_dialect("trino")
    ws = create_autospec(WorkspaceClient)
    scope = "scope"

    with pytest.raises(ValueError, match=f"Unsupported source type --> {engine}"):
        create_adapter(engine, spark, ws, scope)
