from unittest.mock import patch
from databricks.labs.lsql.backends import MockBackend
from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.lakebridge.contexts.application import ApplicationContext

from databricks.labs.lakebridge.deployment.upgrade_common import (
    replace_patterns,
    extract_columns_with_datatype,
    extract_column_name,
    table_original_query,
    current_table_columns,
    installed_table_columns,
    check_table_mismatch,
    recreate_table_sql,
)


def test_replace_patterns_removes_struct_and_map():
    sql_text = "CREATE TABLE test (id INT, data STRUCT<name: STRING, age: INT>, map_data MAP<STRING, INT>)"
    result = replace_patterns(sql_text)
    assert result == "CREATE TABLE test (id INT, data , map_data )"


def test_extract_columns_with_datatype_parses_columns():
    sql_text = "CREATE TABLE test (id INT, name STRING NOT NULL, age INT)"
    result = extract_columns_with_datatype(sql_text)
    assert result == ["id INT", " name STRING NOT NULL", " age INT"]


def test_extract_column_name_parses_column_name():
    column_with_datatype = "id INT"
    result = extract_column_name(column_with_datatype)
    assert result == "id"


def test_table_original_query():
    table_name = "main"
    full_table_name = "main_table"
    result = table_original_query(table_name, full_table_name)
    assert "CREATE OR REPLACE TABLE main_table" in result


def test_current_table_columns():
    table_name = "main"
    full_table_name = "main_table"
    result = current_table_columns(table_name, full_table_name)
    assert result == [
        "recon_table_id",
        "recon_id",
        "source_type",
        "source_table",
        "target_table",
        "report_type",
        "operation_name",
        "start_ts",
        "end_ts",
    ]


def test_installed_table_columns(mock_workspace_client):
    table_identifier = "main_table"
    with patch(
        'databricks.labs.lakebridge.helpers.db_sql.get_sql_backend',
        return_value=MockBackend(),
    ):
        result = installed_table_columns(mock_workspace_client, table_identifier)
        assert result == []


def test_check_table_mismatch():
    main_columns = [
        "recon_table_id",
        "recon_id",
        "source_type",
        "source_table",
        "target_table",
        "report_type",
        "operation_name",
        "start_ts",
        "end_ts",
    ]
    installed_columns = [
        "recon_table_id",
        "recon_id",
        "source_type",
        "source_table",
        "target_table",
        "report_type",
        "operation_name",
        "start_ts",
        "end_ts",
    ]
    result = check_table_mismatch(main_columns, installed_columns)
    assert result is False

    main_columns = [
        "recon_table_id",
        "recon_id",
        "source_type",
        "source_table",
        "target_table",
        "report_type",
        "operation_name",
        "start_ts",
        "end_ts",
    ]
    installed_columns = [
        "recon_table_id",
        "recon_id",
        "source_type",
        "source_table",
        "target_table",
        "report_type",
        "operation_name",
        "start_ts",
    ]
    result = check_table_mismatch(main_columns, installed_columns)
    assert result


def test_recreate_table_sql(mock_workspace_client):
    ## Test 1
    main_columns = [
        "recon_table_id",
        "recon_id",
        "source_type",
        "source_table",
        "target_table",
        "report_type",
        "operation_name",
        "start_ts",
    ]
    installed_columns = [
        "recon_table_id",
        "recon_id",
        "source_type",
        "source_table",
        "target_table",
        "report_type",
        "operation_name",
        "start_ts",
        "end_ts",
    ]
    table_identifier = "main"
    prompts = MockPrompts(
        {
            rf"The `{table_identifier}` table columns are not as expected. Do you want to recreate the `{table_identifier}` table?": "yes"
        }
    )
    installation = MockInstallation()
    ctx = ApplicationContext(mock_workspace_client)
    ctx.replace(
        prompts=prompts,
        installation=installation,
    )
    result = recreate_table_sql(table_identifier, main_columns, installed_columns, ctx.prompts)
    assert "CREATE OR REPLACE TABLE main" in result

    ## Test 2
    prompts = MockPrompts(
        {
            rf"The `{table_identifier}` table columns are not as expected. Do you want to recreate the `{table_identifier}` table?": "no"
        }
    )
    installation = MockInstallation()
    ctx = ApplicationContext(mock_workspace_client)
    ctx.replace(
        prompts=prompts,
        installation=installation,
    )
    result = recreate_table_sql(table_identifier, main_columns, installed_columns, ctx.prompts)
    assert result is None

    ## Test 3
    main_columns = [
        "recon_table_id",
        "recon_id",
        "source_type",
        "source_table",
        "target_table",
        "report_type",
        "operation_name",
        "start_ts",
        "end_ts",
    ]
    installed_columns = [
        "recon_table_id",
        "recon_id",
        "source_type",
        "source_table",
        "target_table",
        "report_type",
        "operation_name",
        "start_ts",
        "end_ts",
    ]
    table_identifier = "main"
    prompts = MockPrompts(
        {
            rf"The `{table_identifier}` table columns are not as expected. Do you want to recreate the `{table_identifier}` table?": "yes"
        }
    )
    result = recreate_table_sql(table_identifier, main_columns, installed_columns, prompts)
    assert (
        result
        == "CREATE OR REPLACE TABLE main AS SELECT recon_table_id,recon_id,source_type,source_table,target_table,report_type,operation_name,start_ts,end_ts FROM main"
    )
