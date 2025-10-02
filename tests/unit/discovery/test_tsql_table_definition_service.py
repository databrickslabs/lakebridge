from unittest.mock import MagicMock

from databricks.labs.lakebridge.connections.database_manager import FetchResult
from databricks.labs.lakebridge.discovery.table import TableDefinition
from databricks.labs.lakebridge.discovery.tsql_table_definition import TsqlTableDefinitionService


def test_get_table_definition_with_data():
    db_manager = MagicMock()
    mock_result = [
        (
            "catalog1",
            "schema1",
            "table1",
            "/path/to/table",
            "parquet",
            "",
            "col1§int§YES§Primary Column‡col2§string§NO§Description",
            10.5,
            "Table Comment",
            "col1:col2",
        ),
    ]

    mock_column_names = [
        "TABLE_CATALOG",
        "TABLE_SCHEMA",
        "TABLE_NAME",
        "location",
        "TABLE_FORMAT",
        "view_definition",
        "DERIVED_SCHEMA",
        "SIZE_GB",
        "TABLE_COMMENT",
        "PK_COLUMN_NAME",
    ]

    mock_query_result = MagicMock()
    mock_query_result.__iter__.return_value = iter(mock_result)
    db_manager.fetch.return_value = FetchResult(mock_column_names, mock_result)

    tss = TsqlTableDefinitionService(db_manager)
    result = list(tss.get_table_definition("test_catalog"))
    assert result[0].primary_keys == ['col1', 'col2']
    assert isinstance(result[0], TableDefinition)
    assert result[0].fqn.catalog == 'catalog1'
    assert result[0].fqn.schema == 'schema1'
    assert result[0].fqn.name == 'table1'


def test_get_catalogs():
    db_manager = MagicMock()
    db_manager.fetch.return_value = FetchResult([], [('db1',), ('db2',)])
    tss = TsqlTableDefinitionService(db_manager)
    result = list(tss.get_all_catalog())
    assert result == ['db1', 'db2']
