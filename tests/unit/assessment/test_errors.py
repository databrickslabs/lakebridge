import pytest

from databricks.labs.lakebridge.assessments.errors import ErrorCategory, classify_sqlstate


@pytest.mark.parametrize(
    ("sqlstate", "message", "expected"),
    [
        # SQLSTATE class prefixes
        ("08001", "", ErrorCategory.CONNECTION),
        ("08006", "", ErrorCategory.CONNECTION),
        ("28000", "", ErrorCategory.AUTH),
        ("28P01", "", ErrorCategory.AUTH),
        # Explicit absence / permission / syntax states
        ("42P01", "", ErrorCategory.ABSENCE),
        ("42703", "", ErrorCategory.ABSENCE),
        ("42S02", "", ErrorCategory.ABSENCE),
        ("3F000", "", ErrorCategory.ABSENCE),
        ("42501", "", ErrorCategory.PERMISSION),
        ("42601", "", ErrorCategory.SYNTAX),
        # Teradata numeric fallback parsed from the message when SQLSTATE is absent
        (None, "[Error 3807] Object 'FOO' does not exist", ErrorCategory.ABSENCE),
        (None, "[Error 3523] The user does not have privilege", ErrorCategory.PERMISSION),
        # SQLSTATE takes precedence over the message fallback
        ("42601", "[Error 3807] misleading", ErrorCategory.SYNTAX),
        # Nothing recognizable
        (None, "", ErrorCategory.UNKNOWN),
        (None, "[Error 9999] something else", ErrorCategory.UNKNOWN),
        ("99999", "unmapped sqlstate", ErrorCategory.UNKNOWN),
    ],
)
def test_classify_sqlstate(sqlstate: str | None, message: str, expected: ErrorCategory) -> None:
    assert classify_sqlstate(sqlstate, message) == expected
