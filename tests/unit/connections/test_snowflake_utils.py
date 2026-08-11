"""Unit tests for the Snowflake account-URL helper and connector URL building."""

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

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


def test_snowflake_url_happy_path():
    url, connect_args = SnowflakeConnector._build_engine_args(
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
    rendered = url.render_as_string(hide_password=False)
    assert rendered.startswith("snowflake://svc_user:plain_token@MYORG-MYACCOUNT/SNOWFLAKE/ACCOUNT_USAGE")
    assert "warehouse=WH" in rendered
    assert "role=SYSADMIN" in rendered
    assert connect_args == {}


def test_snowflake_url_escapes_pat_special_chars():
    # PATs are base64url and routinely contain '/', '=', '@'. Those must be
    # percent-escaped so SQLAlchemy doesn't misread them as URL structure
    # (path separator, host delimiter, etc.).
    url, connect_args = SnowflakeConnector._build_engine_args(
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
    rendered = url.render_as_string(hide_password=False)
    # The structural characters are escaped inside the password.
    assert "ab%2Fcd%3Def%40ij%25kl" in rendered
    # And the host is still parsed correctly despite the '@' in the password.
    assert "@MYORG-MYACCOUNT/SNOWFLAKE/ACCOUNT_USAGE" in rendered
    assert connect_args == {}


def _write_unencrypted_private_key(path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def test_snowflake_key_pair_builds_passwordless_url_with_private_key(tmp_path):
    key_path = tmp_path / "rsa_key.p8"
    _write_unencrypted_private_key(key_path)

    url, connect_args = SnowflakeConnector._build_engine_args(
        {
            "auth_type": "key_pair",
            "account": "MYORG-MYACCOUNT",
            "user": "svc_user",
            "private_key_path": str(key_path),
            "warehouse": "WH",
            "database": "SNOWFLAKE",
            "schema": "ACCOUNT_USAGE",
            "role": "SYSADMIN",
        }
    )
    rendered = url.render_as_string(hide_password=False)
    assert rendered.startswith("snowflake://svc_user@MYORG-MYACCOUNT/SNOWFLAKE/ACCOUNT_USAGE")
    assert url.password is None
    assert set(connect_args) == {"private_key"}
    assert isinstance(connect_args["private_key"], bytes)
    assert connect_args["private_key"]  # non-empty DER PKCS8


def test_connector_raises_when_account_missing():
    with pytest.raises(KeyError):
        SnowflakeConnector({"connection": {"user": "svc_user", "pat": "token"}})
