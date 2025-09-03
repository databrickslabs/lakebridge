import datetime
import re
from pathlib import Path

import pytest

from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge import cli


@pytest.fixture
def temp_dirs_for_lineage(empty_input_source: Path, output_folder: Path) -> tuple[Path, Path]:
    sample_sql_file = empty_input_source / "sample.sql"
    sample_sql_content = """
    create table table1 select * from table2 inner join
    table3 on table2.id = table3.id where table2.id in (select id from table4);
    create table table2 select * from table4;
    create table table5 select * from table3 join table4 on table3.id = table4.id;
    """
    sample_sql_file.write_text(sample_sql_content, encoding="utf-8")

    output_folder.mkdir()

    return empty_input_source, output_folder


def test_generate_lineage_valid_input(
    mock_workspace_client: WorkspaceClient, temp_dirs_for_lineage: tuple[Path, Path]
) -> None:
    input_dir, output_dir = temp_dirs_for_lineage
    cli.generate_lineage(
        mock_workspace_client,
        source_dialect="snowflake",
        input_source=str(input_dir),
        output_folder=str(output_dir),
    )

    date_str = datetime.datetime.now().strftime("%d%m%y")
    output_filename = f"lineage_{date_str}.dot"
    output_file = output_dir / output_filename
    assert output_file.is_file()
    expected_output = """
    flowchart TD
    Table1 --> Table2
    Table1 --> Table3
    Table1 --> Table4
    Table2 --> Table4
    Table3
    Table4
    Table5 --> Table3
    Table5 --> Table4
    """
    actual_output = output_file.read_text()
    assert actual_output.strip() == expected_output.strip()


def test_generate_lineage_with_invalid_dialect(
    mock_workspace_client: WorkspaceClient,
    empty_input_source: Path,
    output_folder: Path,
) -> None:
    output_folder.mkdir()
    expected_error = (
        r"Unsupported source dialect provided for '--source-dialect': 'invalid_dialect' \(supported: (\w+(, )?)+\)"
    )
    with pytest.raises(ValueError, match=expected_error):
        cli.generate_lineage(
            w=mock_workspace_client,
            source_dialect="invalid_dialect",
            input_source=str(empty_input_source),
            output_folder=str(output_folder),
        )


def test_generate_lineage_invalid_input_source(mock_workspace_client: WorkspaceClient, output_folder: Path) -> None:
    output_folder.mkdir()
    expected_error = "Invalid path for '--input-source': Path '/path/to/invalid/sql/file.sql' does not exist."
    with pytest.raises(ValueError, match=re.escape(expected_error)):
        cli.generate_lineage(
            w=mock_workspace_client,
            source_dialect="snowflake",
            input_source="/path/to/invalid/sql/file.sql",
            output_folder=str(output_folder),
        )


def test_generate_lineage_invalid_output_dir(mock_workspace_client: WorkspaceClient, empty_input_source: Path) -> None:
    expected_error = "Invalid path for '--output-folder': Path '/does/not/exist' does not exist."
    with pytest.raises(ValueError, match=re.escape(expected_error)):
        cli.generate_lineage(
            w=mock_workspace_client,
            source_dialect="snowflake",
            input_source=str(empty_input_source),
            output_folder="/does/not/exist",
        )
