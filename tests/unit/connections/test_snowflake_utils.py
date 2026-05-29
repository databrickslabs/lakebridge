"""Unit tests for the Snowflake account-URL helper and connector URL building."""

from unittest.mock import patch

import pytest

from databricks.labs.lakebridge.connections.database_manager import SnowflakeConnector
from databricks.labs.lakebridge.connections.snowflake_utils import (
    parse_snowflake_account,
    is_valid_snowflake_account,
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
    "identifier, valid",
    [
        ("MYORG-MYACCOUNT", True),
        ("xy12345.us-east-1.aws", True),  # legacy locator with region/cloud
        ("abc_123", True),
        ("", False),
        ("my org-acct", False),  # space
        ("acct/with/slash", False),
    ],
)
def test_is_valid_snowflake_account(identifier, valid):
    assert is_valid_snowflake_account(identifier) is valid


def test_connector_rejects_malformed_account():
    with pytest.raises(ConnectionError, match="Invalid Snowflake account identifier"):
        SnowflakeConnector(
            {
                "connection": {
                    "account": "my org-acct",  # space breaks the URL
                    "user": "svc_user",
                    "pat": "token",
                }
            }
        )


def _build_url(connection: dict) -> str:
    """Construct the SQLAlchemy URL the connector would use, without opening a connection."""
    captured = {}

    def _capture(url):
        captured["url"] = url
        return object()  # stand-in engine; never used

    with patch("databricks.labs.lakebridge.connections.database_manager.create_engine", side_effect=_capture):
        SnowflakeConnector({"connection": connection})
    return captured["url"].render_as_string(hide_password=False)


def test_snowflake_url_happy_path():
    url = _build_url(
        {
            "account": "https://MYORG-MYACCOUNT.snowflakecomputing.com",
            "user": "svc_user",
            "pat": "plain_token",
            "warehouse": "WH",
            "database": "SNOWFLAKE",
            "schema": "ACCOUNT_USAGE",
            "role": "SYSADMIN",
        }
    )
    assert url.startswith("snowflake://svc_user:plain_token@MYORG-MYACCOUNT/SNOWFLAKE/ACCOUNT_USAGE")
    assert "warehouse=WH" in url
    assert "role=SYSADMIN" in url


def test_snowflake_url_escapes_pat_special_chars():
    # PATs are base64url and routinely contain '/', '=', '@'. Those must be
    # percent-escaped so SQLAlchemy doesn't misread them as URL structure
    # (path separator, host delimiter, etc.).
    url = _build_url(
        {
            "account": "MYORG-MYACCOUNT",
            "user": "svc_user",
            "pat": "ab/cd=ef@ij%kl",
            "warehouse": "WH",
            "database": "SNOWFLAKE",
            "schema": "ACCOUNT_USAGE",
            "role": "SYSADMIN",
        }
    )
    # The structural characters are escaped inside the password.
    assert "ab%2Fcd%3Def%40ij%25kl" in url
    # And the host is still parsed correctly despite the '@' in the password.
    assert "@MYORG-MYACCOUNT/SNOWFLAKE/ACCOUNT_USAGE" in url


def test_connector_raises_when_account_missing():
    with pytest.raises(KeyError):
        SnowflakeConnector({"connection": {"user": "svc_user", "pat": "token"}})
