import datetime
from unittest.mock import patch

import pytest


from databricks.labs.lakebridge import cli


@pytest.fixture
def temp_dirs_for_lineage(tmpdir):
    input_dir = tmpdir.mkdir("input")
    output_dir = tmpdir.mkdir("output")

    sample_sql_file = input_dir.join("sample.sql")
    sample_sql_content = """
    create table table1 select * from table2 inner join
    table3 on table2.id = table3.id where table2.id in (select id from table4);
    create table table2 select * from table4;
    create table table5 select * from table3 join table4 on table3.id = table4.id;
    """
    sample_sql_file.write(sample_sql_content)

    return input_dir, output_dir


def test_generate_lineage_valid_input(temp_dirs_for_lineage, mock_workspace_client):
    input_dir, output_dir = temp_dirs_for_lineage
    cli.generate_lineage(
        mock_workspace_client,
        source_dialect="snowflake",
        input_source=str(input_dir),
        output_folder=str(output_dir),
    )

    date_str = datetime.datetime.now().strftime("%d%m%y")
    output_filename = f"lineage_{date_str}.dot"
    output_file = output_dir.join(output_filename)
    assert output_file.check(file=1)
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
    actual_output = output_file.read()
    assert actual_output.strip() == expected_output.strip()


def test_generate_lineage_with_invalid_dialect(mock_workspace_client):
    with pytest.raises(Exception, match="Invalid value for '--source-dialect'"):
        cli.generate_lineage(
            mock_workspace_client,
            source_dialect="invalid_dialect",
            input_source="/path/to/sql/file.sql",
            output_folder="/path/to/output",
        )


def test_generate_lineage_invalid_input_source(mock_workspace_client):
    with (
        patch("os.path.exists", return_value=False),
        pytest.raises(Exception, match="Invalid value for '--input-source'"),
    ):
        cli.generate_lineage(
            mock_workspace_client,
            source_dialect="snowflake",
            input_source="/path/to/invalid/sql/file.sql",
            output_folder="/path/to/output",
        )


def test_generate_lineage_invalid_output_dir(mock_workspace_client, monkeypatch):
    input_source = "/path/to/sql/file.sql"
    output_folder = "/path/to/output"
    monkeypatch.setattr("os.path.exists", lambda x: x == input_source)
    with pytest.raises(Exception, match="Invalid value for '--output-folder'"):
        cli.generate_lineage(
            mock_workspace_client,
            source_dialect="snowflake",
            input_source=input_source,
            output_folder=output_folder,
        )
