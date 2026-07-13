import pytest

from databricks.labs.lakebridge.assessments.errors import ErrorCategory, classify_standard_sqlstate


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [
        ("08001", ErrorCategory.CONNECTION),
        ("08006", ErrorCategory.CONNECTION),
        ("28000", ErrorCategory.AUTH),
        ("28P01", ErrorCategory.AUTH),
        ("42P01", ErrorCategory.ABSENCE),
        ("42703", ErrorCategory.ABSENCE),
        ("42S02", ErrorCategory.ABSENCE),
        ("3F000", ErrorCategory.ABSENCE),
        ("42501", ErrorCategory.PERMISSION),
        ("42601", ErrorCategory.SYNTAX),
        (None, None),
        ("99999", None),
    ],
)
def test_classify_standard_sqlstate(sqlstate: str | None, expected: ErrorCategory | None) -> None:
    assert classify_standard_sqlstate(sqlstate) == expected
