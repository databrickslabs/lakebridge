# pylint: disable=all
import json
import os
from datetime import datetime
from unittest import mock
from unittest.mock import patch

import pytest
import pytz

from databricks.labs.lakebridge.coverage.commons import (
    ReportEntry,
    collect_transpilation_stats,
    get_current_commit_hash,
    get_current_time_utc,
    get_env_var,
    get_supported_sql_files,
    write_json_line,
)
from databricks.labs.lakebridge.transpiler.sqlglot.generator.databricks import Databricks
from databricks.labs.lakebridge.transpiler.sqlglot.parsers.snowflake import Snowflake


def test_get_supported_sql_files(tmp_path):
    sub_dir = tmp_path / "test_dir"
    sub_dir.mkdir()

    files = [
        tmp_path / "test1.sql",
        tmp_path / "test2.sql",
        tmp_path / "test3.ddl",
        tmp_path / "test4.txt",
        sub_dir / "test5.sql",
    ]

    for file in files:
        file.touch()

    files = list(get_supported_sql_files(tmp_path))
    assert len(files) == 4
    assert all(file.is_file() for file in files)


def test_write_json_line(tmp_path):
    report_entry = ReportEntry(
        project="Remorph",
        commit_hash="6b0e403",
        version="",
        timestamp="2022-01-01T00:00:00",
        source_dialect="Snowflake",
        target_dialect="Databricks",
        file="test_file.sql",
    )
    report_file_path = tmp_path / "test_file.json"
    with open(report_file_path, "w", encoding="utf8") as report_file:
        write_json_line(report_file, report_entry)

    with open(report_file_path) as report_file:
        retrieved_report_entry = ReportEntry(**json.loads(report_file.readline()))
        assert retrieved_report_entry == report_entry


def test_get_env_var():
    os.environ["TEST_VAR"] = "test_value"
    assert get_env_var("TEST_VAR") == "test_value"
    with pytest.raises(ValueError):
        get_env_var("NON_EXISTENT_VAR", required=True)


def test_get_current_commit_hash():
    with patch("subprocess.check_output", return_value="6b0e403".encode("ascii")):
        assert get_current_commit_hash() == "6b0e403"

    with patch("subprocess.check_output", side_effect=FileNotFoundError()):
        assert get_current_commit_hash() is None


@mock.patch("databricks.labs.lakebridge.coverage.commons.datetime")
def test_get_current_time_utc(mock_datetime):
    fixed_timestamp = datetime(2022, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)
    mock_datetime.now = mock.Mock(return_value=fixed_timestamp)
    assert get_current_time_utc() == fixed_timestamp


def test_stats_collection_with_invalid_input(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"

    with pytest.raises(NotADirectoryError, match="The input path .* doesn't exist"):
        collect_transpilation_stats(
            project="Remorph",
            commit_hash="6b0e403",
            version="",
            source_dialect=Snowflake,
            target_dialect=Databricks,
            input_dir=input_dir,
            result_dir=output_dir,
        )


def test_stats_collection_with_invalid_output_dir(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.touch()

    with pytest.raises(NotADirectoryError, match="The output path .* exists but is not a directory"):
        collect_transpilation_stats(
            project="Remorph",
            commit_hash="6b0e403",
            version="",
            source_dialect=Snowflake,
            target_dialect=Databricks,
            input_dir=input_dir,
            result_dir=output_dir,
        )


def test_stats_collection_with_valid_io_dir(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    try:
        collect_transpilation_stats(
            project="Remorph",
            commit_hash="6b0e403",
            version="",
            source_dialect=Snowflake,
            target_dialect=Databricks,
            input_dir=input_dir,
            result_dir=output_dir,
        )
    except Exception as e:
        pytest.fail(f"Transpilation stats collection raised an unexpected exception {e!s}")


def test_stats_collection_with_parse_error(io_dir_pair):
    input_dir, output_dir = io_dir_pair
    fixed_timestamp = datetime(2022, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)

    with (
        patch(
            "databricks.labs.lakebridge.coverage.commons.parse_sql",
            side_effect=Exception("Some parse error"),
        ),
        patch(
            "databricks.labs.lakebridge.coverage.commons.get_current_time_utc",
            return_value=fixed_timestamp,
        ),
    ):
        collect_transpilation_stats(
            project="Remorph",
            commit_hash="6b0e403",
            version="",
            source_dialect=Snowflake,
            target_dialect=Databricks,
            input_dir=input_dir,
            result_dir=output_dir,
        )

        report_files = list(output_dir.glob("*.json"))
        assert len(report_files) == 1

        expected_report_entry = ReportEntry(
            project="Remorph",
            commit_hash="6b0e403",
            version="",
            timestamp=fixed_timestamp.isoformat(),
            source_dialect="Snowflake",
            target_dialect="Databricks",
            file="input/test.sql",
            failures=[{'error_code': "Exception", 'error_message': "Exception('Some parse error')"}],
        )
        retrieved_report_entry = ReportEntry(**json.loads(report_files[0].read_text()))
        assert retrieved_report_entry == expected_report_entry


def test_stats_collection_with_transpile_error(io_dir_pair):
    input_dir, output_dir = io_dir_pair
    fixed_timestamp = datetime(2022, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)

    with (
        patch(
            "databricks.labs.lakebridge.coverage.commons.generate_sql",
            side_effect=Exception("Some transpilation error"),
        ),
        patch(
            "databricks.labs.lakebridge.coverage.commons.get_current_time_utc",
            return_value=fixed_timestamp,
        ),
    ):
        collect_transpilation_stats(
            project="Remorph",
            commit_hash="6b0e403",
            version="",
            source_dialect=Snowflake,
            target_dialect=Databricks,
            input_dir=input_dir,
            result_dir=output_dir,
        )

        report_files = list(output_dir.glob("*.json"))
        assert len(report_files) == 1

        expected_report_entry = ReportEntry(
            project="Remorph",
            commit_hash="6b0e403",
            version="",
            timestamp=fixed_timestamp.isoformat(),
            source_dialect="Snowflake",
            target_dialect="Databricks",
            file="input/test.sql",
            parsed=1,
            statements=1,
            failures=[{'error_code': "Exception", 'error_message': "Exception('Some transpilation error')"}],
        )
        retrieved_report_entry = ReportEntry(**json.loads(report_files[0].read_text()))
        assert retrieved_report_entry == expected_report_entry


def test_stats_collection_no_error(io_dir_pair):
    input_dir, output_dir = io_dir_pair
    fixed_timestamp = datetime(2022, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)

    with (
        patch(
            "databricks.labs.lakebridge.coverage.commons.get_current_time_utc",
            return_value=fixed_timestamp,
        ),
    ):
        collect_transpilation_stats(
            project="Remorph",
            commit_hash="6b0e403",
            version="",
            source_dialect=Snowflake,
            target_dialect=Databricks,
            input_dir=input_dir,
            result_dir=output_dir,
        )

        report_files = list(output_dir.glob("*.json"))
        assert len(report_files) == 1

        expected_report_entry = ReportEntry(
            project="Remorph",
            commit_hash="6b0e403",
            version="",
            timestamp=fixed_timestamp.isoformat(),
            source_dialect="Snowflake",
            target_dialect="Databricks",
            file="input/test.sql",
            parsed=1,
            statements=1,
            transpiled=1,
            transpiled_statements=1,
        )
        retrieved_report_entry = ReportEntry(**json.loads(report_files[0].read_text()))
        assert retrieved_report_entry == expected_report_entry
