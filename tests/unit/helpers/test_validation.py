from databricks.labs.lsql.backends import MockBackend
from databricks.labs.lsql.core import Row
from databricks.labs.lakebridge.helpers.validation import Validator


def test_valid_query(transpile_config):
    query = "SELECT * FROM a_table"
    sql_backend = MockBackend(
        rows={
            "EXPLAIN SELECT": [Row(plan="== Physical Plan ==")],
        }
    )
    validator = Validator(sql_backend)
    validation_result = validator.validate_format_result(transpile_config, query)
    assert query in validation_result.validated_sql
    assert validation_result.exception_msg is None


def test_query_with_syntax_error(transpile_config):
    query = "SELECT * a_table"
    sql_backend = MockBackend(
        fails_on_first={
            f"EXPLAIN {query}": "[PARSE_SYNTAX_ERROR] Syntax error at",
        }
    )
    validator = Validator(sql_backend)
    validation_result = validator.validate_format_result(transpile_config, query)
    assert "Exception Start" in validation_result.validated_sql
    assert "Syntax error" in validation_result.exception_msg


def test_query_with_analysis_error(transpile_config):
    error_types = [
        ("[TABLE_OR_VIEW_NOT_FOUND]", True),
        ("[TABLE_OR_VIEW_ALREADY_EXISTS]", True),
        ("[UNRESOLVED_ROUTINE]", False),
        ("Hive support is required to CREATE Hive TABLE (AS SELECT).;", True),
        ("Some other analysis error", False),
    ]

    for err, should_succeed in error_types:
        query = "SELECT * FROM a_table"
        sql_backend = MockBackend(
            fails_on_first={
                f"EXPLAIN {query}": err,
            }
        )
        validator = Validator(sql_backend)
        validation_result = validator.validate_format_result(transpile_config, query)
        if should_succeed:
            assert query in validation_result.validated_sql
            assert "[WARNING]:" in validation_result.exception_msg
        else:
            assert err in validation_result.exception_msg


def test_validate_format_result_with_valid_query(transpile_config):
    query = "SELECT current_timestamp()"
    sql_backend = MockBackend(
        rows={
            "EXPLAIN SELECT": [Row(plan="== Physical Plan ==")],
        }
    )
    validator = Validator(sql_backend)
    validation_result = validator.validate_format_result(transpile_config, query)
    assert query in validation_result.validated_sql
    assert validation_result.exception_msg is None


def test_validate_format_result_with_invalid_query(transpile_config):
    query = "SELECT fn() FROM tab"
    sql_backend = MockBackend(
        rows={
            "EXPLAIN SELECT": [
                Row(plan="Error occurred during query planning:"),
                Row(plan="[UNRESOLVED_ROUTINE] Cannot resolve function"),
            ],
        }
    )
    validator = Validator(sql_backend)
    validation_result = validator.validate_format_result(transpile_config, query)
    assert "Exception Start" in validation_result.validated_sql
    assert "[UNRESOLVED_ROUTINE]" in validation_result.exception_msg


def test_validate_with_no_rows_returned(transpile_config):
    query = "SELECT * FROM a_table"
    sql_backend = MockBackend(
        rows={
            "EXPLAIN SELECT": [],
        }
    )
    validator = Validator(sql_backend)
    validation_result = validator.validate_format_result(transpile_config, query)
    assert "Exception Start" in validation_result.validated_sql
    assert "No results returned" in validation_result.exception_msg
