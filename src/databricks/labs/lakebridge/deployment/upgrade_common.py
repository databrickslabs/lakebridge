import logging
import re
from importlib.resources import files

import databricks.labs.lakebridge.resources

from databricks.labs.blueprint.tui import Prompts
from databricks.sdk import WorkspaceClient
from databricks.labs.lakebridge.helpers import db_sql

logger = logging.getLogger(__name__)


def replace_patterns(sql_text: str) -> str:
    """
    Replace the STRUCT and MAP datatypes in the SQL text with empty string
    """
    # Pattern to match nested STRUCT and MAP datatypes
    pattern = r'(STRUCT<[^<>]*?(?:<[^<>]*?>[^<>]*?)*>|MAP<[^<>]*?(?:<[^<>]*?>[^<>]*?)*>)'
    parsed_sql_text = re.sub(pattern, "", sql_text, flags=re.DOTALL)
    return parsed_sql_text


def extract_columns_with_datatype(sql_text: str) -> list[str]:
    """
    Extract the columns with datatype from the SQL text
    Example:
        Input: CREATE TABLE main (
            recon_table_id BIGINT NOT NULL,
            report_type STRING NOT NULL
            );
       Output:  [recon_table_id BIGINT NOT NULL,
                      report_type STRING NOT NULL]
    """
    return sql_text[sql_text.index("(") + 1 : sql_text.index(")")].strip().split(",")


def extract_column_name(column_with_datatype: str) -> str:
    """
    Extract the column name from the column with datatype.
    Example:
        Input: \n    recon_table_id BIGINT NOT NULL,
        Output: recon_table_id
    """
    return column_with_datatype.strip("\n").strip().split(" ")[0]


def table_original_query(table_name: str, full_table_name: str) -> str:
    """
    Get the main table DDL from the main.sql file
    :return: str
    """
    resources = files(databricks.labs.lakebridge.resources)
    query_dir = resources.joinpath("reconcile/queries/installation")
    return (
        query_dir.joinpath(f"{table_name}.sql")
        .read_text()
        .replace(f"CREATE TABLE IF NOT EXISTS {table_name}", f"CREATE OR REPLACE TABLE {full_table_name}")
    )


def current_table_columns(table_name: str, full_table_name: str) -> list[str]:
    """
    Extract the column names from the main table DDL
    :return: column_names: list[str]
    """
    main_sql = replace_patterns(table_original_query(table_name, full_table_name))
    main_table_columns = [
        extract_column_name(main_table_column) for main_table_column in extract_columns_with_datatype(main_sql)
    ]
    return main_table_columns


def installed_table_columns(ws: WorkspaceClient, table_identifier: str) -> list[str]:
    """
    Fetch the column names from the installed table on Databricks Workspace using SQL Backend
    :return: column_names: list[str]
    """
    main_table_columns = list(db_sql.get_sql_backend(ws).fetch(f"DESC {table_identifier}"))
    return [row.col_name for row in main_table_columns]


def check_table_mismatch(
    installed_table,
    current_table,
) -> bool:
    # Compare the current main table columns with the installed main table columns
    if len(installed_table) != len(current_table) or sorted(installed_table) != sorted(current_table):
        return True
    return False


def recreate_table_sql(
    table_identifier: str,
    installed_table: list[str],
    current_table: list[str],
    prompts: Prompts,
) -> str | None:
    """
    * Verify all the current main table columns are present in the installed main table and then use CTAS to recreate the main table
       * If any of the current main table columns are missing in the installed main table, prompt the user to recreate the main table:
            - If the user confirms, recreate the main table using the main DDL file, else log an error message and exit
    :param table_identifier:
    :param installed_table:
    :param current_table:
    :param prompts:
    :return:
    """
    table_name = table_identifier.split('.')[-1]
    sql: str | None = (
        f"CREATE OR REPLACE TABLE {table_identifier} AS SELECT {','.join(current_table)} FROM {table_identifier}"
    )

    if not set(current_table).issubset(installed_table):
        if prompts.confirm(
            f"The `{table_identifier}` table columns are not as expected. Do you want to recreate the `{table_identifier}` table?"
        ):
            sql = table_original_query(table_name, table_identifier)
        else:
            logger.error(
                f"The `{table_identifier}` table columns are not as expected. Please check and recreate the `{table_identifier}` table."
            )
            sql = None
    return sql
