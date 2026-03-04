from unittest.mock import MagicMock, patch

import duckdb
import pytest

from databricks.labs.lakebridge.assessments.dashboards.execute import (  # pylint: disable=import-private-name
    ExtractIngestionError,
    _get_extract_tables,
    _ingest_profiler_tables,
    _ingest_table,
    _validate_profiler_extract,
)


_SYNAPSE_SCHEMA_MINIFIED = """\
source_tech: synapse
version: 0.1
schemas:
  main:
    tables:
      table_a:
        columns:
          - name: id
            type: VARCHAR
  other_schema:
    tables:
      table_b:
        columns:
          - name: name
            type: BIGINT
"""

_INVALID_SCHEMA = ": [\n this is an example of a bad schema file {{"


@pytest.fixture
def duckdb_with_table(tmp_path):
    """DuckDB file containing one populated table."""
    db_path = str(tmp_path / "test.db")
    with duckdb.connect(db_path) as conn:
        conn.execute("CREATE SCHEMA test_schema")
        conn.execute("CREATE TABLE test_schema.test_table (id INTEGER, name VARCHAR)")
        conn.execute("INSERT INTO test_schema.test_table VALUES (1, 'a'), (2, 'b')")
    return db_path


@pytest.fixture
def empty_duckdb(tmp_path):
    """DuckDB file with no tables."""
    db_path = str(tmp_path / "empty.db")
    with duckdb.connect(db_path):
        pass
    return db_path


@pytest.fixture
def duckdb_with_two_tables(tmp_path):
    """DuckDB file with two populated tables."""
    db_path = str(tmp_path / "multi.db")
    with duckdb.connect(db_path) as conn:
        conn.execute("CREATE SCHEMA extract_schema")
        conn.execute("CREATE TABLE extract_schema.table1 (id INTEGER)")
        conn.execute("INSERT INTO extract_schema.table1 VALUES (1)")
        conn.execute("CREATE TABLE extract_schema.table2 (id INTEGER)")
        conn.execute("INSERT INTO extract_schema.table2 VALUES (2)")
    return db_path


def test_get_extract_tables_returns_tuples(tmp_path):
    schema_file = tmp_path / "schema.yml"
    schema_file.write_text(_SYNAPSE_SCHEMA_MINIFIED)

    tables = _get_extract_tables(schema_file)

    assert len(tables) == 2
    assert ("main", "table_a", "main.table_a") in tables
    assert ("other_schema", "table_b", "other_schema.table_b") in tables


def test_get_extract_tables_empty_schemas(tmp_path):
    schema_file = tmp_path / "schema.yml"
    schema_file.write_text("schemas: {}")

    tables = _get_extract_tables(schema_file)

    assert not list(tables)


def test_get_extract_tables_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="Schema definition not found"):
        _get_extract_tables(tmp_path / "missing.yml")


def test_get_extract_tables_invalid_yaml(tmp_path):
    schema_file = tmp_path / "bad.yml"
    schema_file.write_text(_INVALID_SCHEMA)

    with pytest.raises(ValueError, match="Could not read extract schema definition"):
        _get_extract_tables(schema_file)


def test_ingest_table_success(duckdb_with_table):
    mock_spark = MagicMock()
    mock_df = MagicMock()
    mock_spark.createDataFrame.return_value = mock_df

    with patch("databricks.labs.lakebridge.assessments.dashboards.execute.SparkSession") as mock_session_cls:
        mock_session_cls.builder.getOrCreate.return_value = mock_spark
        _ingest_table(duckdb_with_table, "test_schema.test_table", "my_catalog.my_schema.test_table")

    mock_spark.createDataFrame.assert_called_once()
    mock_df.write.format("delta").mode("overwrite").saveAsTable.assert_called_once_with(
        "my_catalog.my_schema.test_table"
    )


def test_ingest_table_missing_table_raises_catalog_exception(empty_duckdb):
    with pytest.raises(duckdb.CatalogException):
        _ingest_table(empty_duckdb, "nonexistent_schema.nonexistent_table", "catalog.schema.table")


def test_ingest_table_io_exception():
    with patch("databricks.labs.lakebridge.assessments.dashboards.execute.duckdb.connect") as mock_connect:
        mock_connect.side_effect = duckdb.IOException("Cannot open file")
        with pytest.raises(duckdb.IOException):
            _ingest_table("/bad/path.db", "schema.table", "catalog.schema.table")


def test_ingest_table_spark_error_raises_extract_ingest_error(duckdb_with_table):
    with patch("databricks.labs.lakebridge.assessments.dashboards.execute.SparkSession") as mock_session_cls:
        mock_spark = MagicMock()
        mock_session_cls.builder.getOrCreate.return_value = mock_spark
        mock_spark.createDataFrame.side_effect = RuntimeError("Spark cluster unavailable")

        with pytest.raises(ExtractIngestionError, match="Unable to ingest table"):
            _ingest_table(duckdb_with_table, "test_schema.test_table", "catalog.schema.table")


def test_ingest_profiler_tables_calls_ingest_for_each_table(duckdb_with_two_tables):
    with patch("databricks.labs.lakebridge.assessments.dashboards.execute._ingest_table") as mock_ingest:
        _ingest_profiler_tables("my_catalog", "my_schema", duckdb_with_two_tables)

    assert mock_ingest.call_count == 2


def test_ingest_profiler_tables_empty_extract_raises(empty_duckdb):
    with pytest.raises(ValueError, match="Profiler extract contains no tables"):
        _ingest_profiler_tables("catalog", "schema", empty_duckdb)


def test_ingest_profiler_tables_io_exception_on_open():
    with patch("databricks.labs.lakebridge.assessments.dashboards.execute.duckdb.connect") as mock_connect:
        mock_connect.side_effect = duckdb.IOException("Cannot open")
        with pytest.raises(duckdb.IOException):
            _ingest_profiler_tables("catalog", "schema", "/bad.db")


def test_ingest_profiler_tables_per_table_error_does_not_abort(duckdb_with_two_tables):
    """A failure on one table should be logged and skipped, not abort the loop."""
    calls = []

    def failing_first_table(_extract_location, src, _dst):
        calls.append(src)
        if "table1" in src:
            raise ExtractIngestionError("table1 failed")

    with patch(
        "databricks.labs.lakebridge.assessments.dashboards.execute._ingest_table",
        side_effect=failing_first_table,
    ):
        _ingest_profiler_tables("catalog", "schema", duckdb_with_two_tables)

    assert len(calls) == 2


def test_ingest_profiler_tables_duckdb_error_per_table_continues(duckdb_with_two_tables):
    """duckdb.Error on one table should be caught and loop should continue."""
    calls = []

    def failing_first_table(_extract_location, src, _dst):
        calls.append(src)
        if "table1" in src:
            raise duckdb.Error("duckdb error")

    with patch(
        "databricks.labs.lakebridge.assessments.dashboards.execute._ingest_table",
        side_effect=failing_first_table,
    ):
        _ingest_profiler_tables("catalog", "schema", duckdb_with_two_tables)

    assert len(calls) == 2


def _mock_validation_setup(report_outcomes: list[tuple[str, str]]):
    """
    Returns a tuple of patches and mock objects for _validate_profiler_extract tests.
    report_outcomes: list of (outcome, severity) tuples for the mocked report.
    """
    mock_report = [MagicMock(outcome=o, severity=s) for o, s in report_outcomes]
    mock_df = MagicMock()
    return mock_report, mock_df


def test_validate_profiler_extract_returns_true_when_no_errors(empty_duckdb):
    mock_report, mock_df = _mock_validation_setup([("PASS", "WARN"), ("PASS", "WARN")])

    with (
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute._get_extract_tables",
            return_value=[],
        ),
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute.build_validation_report",
            return_value=mock_report,
        ),
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute.build_validation_report_dataframe",
            return_value=mock_df,
        ),
    ):
        result = _validate_profiler_extract("catalog", "schema", empty_duckdb, "synapse")

    assert result is True
    mock_df.write.format("delta").mode("overwrite").saveAsTable.assert_called_once_with(
        "catalog.schema.validation_report"
    )


def test_validate_profiler_extract_returns_false_when_errors_present(empty_duckdb):
    mock_report, mock_df = _mock_validation_setup([("FAIL", "ERROR"), ("PASS", "WARN")])

    with (
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute._get_extract_tables",
            return_value=[],
        ),
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute.build_validation_report",
            return_value=mock_report,
        ),
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute.build_validation_report_dataframe",
            return_value=mock_df,
        ),
    ):
        result = _validate_profiler_extract("catalog", "schema", empty_duckdb, "synapse")

    assert result is False


def test_validate_profiler_extract_warn_failures_do_not_count_as_errors(empty_duckdb):
    mock_report, mock_df = _mock_validation_setup([("FAIL", "WARN"), ("FAIL", "WARN")])

    with (
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute._get_extract_tables",
            return_value=[],
        ),
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute.build_validation_report",
            return_value=mock_report,
        ),
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute.build_validation_report_dataframe",
            return_value=mock_df,
        ),
    ):
        result = _validate_profiler_extract("catalog", "schema", empty_duckdb, "synapse")

    assert result is True


def test_validate_profiler_extract_empty_report_raises(empty_duckdb):
    with (
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute._get_extract_tables",
            return_value=[],
        ),
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute.build_validation_report",
            return_value=[],
        ),
        patch(
            "databricks.labs.lakebridge.assessments.dashboards.execute.build_validation_report_dataframe",
            return_value=MagicMock(),
        ),
    ):
        with pytest.raises(ValueError, match="Profiler extract validation report is empty"):
            _validate_profiler_extract("catalog", "schema", empty_duckdb, "synapse")


def test_validate_profiler_extract_io_exception_propagates():
    with patch("databricks.labs.lakebridge.assessments.dashboards.execute.duckdb.connect") as mock_connect:
        mock_connect.side_effect = duckdb.IOException("File not found")
        with pytest.raises(duckdb.IOException):
            _validate_profiler_extract("catalog", "schema", "/bad/path.db", "synapse")
