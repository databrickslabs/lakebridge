import pytest

from databricks.labs.lakebridge.assessments.errors import (
    ErrorCategory,
    SourceQueryError,
    classify_sqlstate,
)


@pytest.mark.parametrize(
    ("sqlstate", "message", "expected"),
    [
        ("08001", "", ErrorCategory.CONNECTION),
        ("28000", "", ErrorCategory.AUTH),
        ("42P01", "", ErrorCategory.ABSENCE),
        ("42703", "", ErrorCategory.ABSENCE),
        ("42S02", "", ErrorCategory.ABSENCE),
        ("3F000", "", ErrorCategory.ABSENCE),
        ("42501", "", ErrorCategory.PERMISSION),
        ("42601", "", ErrorCategory.SYNTAX),
        (None, "[Error 3807] Object does not exist", ErrorCategory.ABSENCE),
        (None, "[Error 3523] Insufficient privilege", ErrorCategory.PERMISSION),
        (None, "", ErrorCategory.UNKNOWN),
        ("99999", "unmapped sqlstate", ErrorCategory.UNKNOWN),
    ],
)
def test_classify_sqlstate(sqlstate: str | None, message: str, expected: ErrorCategory) -> None:
    assert classify_sqlstate(sqlstate, message) == expected


def test_source_query_error_carries_fields() -> None:
    error = SourceQueryError(ErrorCategory.ABSENCE, "42P01", "relation does not exist", step_name="inventory")
    assert error.category == ErrorCategory.ABSENCE
    assert error.sqlstate == "42P01"
    assert error.reason == "relation does not exist"
    assert error.step_name == "inventory"
    assert str(error) == "relation does not exist"


@pytest.mark.parametrize(
    "category",
    [ErrorCategory.CONNECTION, ErrorCategory.AUTH],
)
def test_source_query_error_is_fatal(category: ErrorCategory) -> None:
    assert SourceQueryError(category, None, "boom").is_fatal()


@pytest.mark.parametrize(
    "category",
    [ErrorCategory.ABSENCE, ErrorCategory.PERMISSION, ErrorCategory.SYNTAX, ErrorCategory.UNKNOWN],
)
def test_source_query_error_is_not_fatal(category: ErrorCategory) -> None:
    assert not SourceQueryError(category, None, "boom").is_fatal()
