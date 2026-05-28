"""Unit tests for Snowflake account-URL helpers."""

import pytest

from databricks.labs.lakebridge.connections.snowflake_utils import (
    parse_snowflake_account,
    validate_snowflake_account,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("MYORG-MYACCOUNT", "MYORG-MYACCOUNT"),
        ("MYORG-MYACCOUNT.snowflakecomputing.com", "MYORG-MYACCOUNT"),
        ("https://MYORG-MYACCOUNT.snowflakecomputing.com", "MYORG-MYACCOUNT"),
        ("https://MYORG-MYACCOUNT.snowflakecomputing.com/", "MYORG-MYACCOUNT"),
        ("https://MYORG-MYACCOUNT.snowflakecomputing.com/console/login", "MYORG-MYACCOUNT"),
        ("", ""),
    ],
)
def test_parse_snowflake_account(raw, expected):
    assert parse_snowflake_account(raw) == expected


@pytest.mark.parametrize(
    "ident, expected",
    [
        ("MYORG-MYACCOUNT", True),
        ("abc_123", True),
        ("", False),
        ("has space", False),
        ("has.dot", False),
    ],
)
def test_validate_snowflake_account(ident, expected):
    assert validate_snowflake_account(ident) is expected
