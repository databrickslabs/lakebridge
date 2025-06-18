from unittest.mock import MagicMock
from databricks.labs.lakebridge.discovery.table import TableDefinition
from databricks.labs.lakebridge.discovery.tsql_table_definition import TsqlTableDefinitionService


def test_get_table_definition_with_data():
    db_manager = MagicMock()
    mock_result = [
        (
            "catalog1",
            "schema1",
            "table1",
            "col1§int§YES§Primary Column‡col2§string§NO§Description",
            "col1:col2",
            "/path/to/table",
            "parquet",
            "",
            10.5,
            "Table Comment",
        ),
    ]

    mock_column_names = [
        "TABLE_CATALOG",
        "TABLE_SCHEMA",
        "TABLE_NAME",
        "DERIVED_SCHEMA",
        "PK_COLUMN_NAME",
        "location",
        "TABLE_FORMAT",
        "view_definition",
        "SIZE_GB",
        "TABLE_COMMENT",
    ]

    mock_query_result = MagicMock()
    mock_query_result.keys.return_value = mock_column_names
    mock_query_result.__iter__.return_value = iter(mock_result)
    db_manager.execute_query.return_value = mock_query_result

    tss = TsqlTableDefinitionService(db_manager)
    result = list(tss.get_table_definition("test_catalog"))
    assert result[0].primary_keys == ['col1', 'col2']
    assert isinstance(result[0], TableDefinition)
    assert result[0].fqn.catalog == 'catalog1'
    assert result[0].fqn.schema == 'schema1'
    assert result[0].fqn.name == 'table1'


def test_get_catalogs():
    db_manager = MagicMock()
    db_manager.connector.execute_query.return_value = [('db1',), ('db2',)]
    tss = TsqlTableDefinitionService(db_manager)
    result = list(tss.get_all_catalog())
    assert result == ['db1', 'db2']
