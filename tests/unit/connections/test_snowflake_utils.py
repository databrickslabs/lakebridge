"""Unit tests for the Snowflake account-URL helper, private-key loader, and connector URL building."""

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from databricks.labs.lakebridge.connections.database_manager import SnowflakeConnector
from databricks.labs.lakebridge.connections.snowflake_utils import (
    load_snowflake_private_key,
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


def _generate_rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_private_key(path: Path, *, passphrase: str | None = None) -> None:
    private_key = _generate_rsa_key()
    if passphrase is None:
        encryption: serialization.KeySerializationEncryption = serialization.NoEncryption()
    else:
        encryption = serialization.BestAvailableEncryption(passphrase.encode())
    path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=encryption,
        )
    )


def test_load_private_key_unencrypted(tmp_path):
    key_path = tmp_path / "rsa_key.p8"
    _write_private_key(key_path, passphrase=None)
    der = load_snowflake_private_key(key_path, passphrase=None)
    assert isinstance(der, bytes)
    assert der


@pytest.mark.parametrize("passphrase", ["secret-pass", "another"])
def test_load_private_key_encrypted_with_passphrase(tmp_path, passphrase):
    key_path = tmp_path / "rsa_key.p8"
    _write_private_key(key_path, passphrase=passphrase)
    der = load_snowflake_private_key(key_path, passphrase=passphrase)
    assert isinstance(der, bytes)
    assert der


def test_load_private_key_empty_passphrase_cannot_decrypt(tmp_path):
    """cryptography treats b'' as a missing password when decrypting, so empty-passphrase
    encrypted keys cannot be loaded. We still pass "" through as b'' (not None) and
    surface a decrypt-oriented error.
    """
    key_path = tmp_path / "rsa_key.p8"
    _write_private_key(key_path, passphrase="not-empty")
    with pytest.raises(ConnectionError, match="Unable to decrypt"):
        load_snowflake_private_key(key_path, passphrase="")


def test_load_private_key_wrong_passphrase(tmp_path):
    key_path = tmp_path / "rsa_key.p8"
    _write_private_key(key_path, passphrase="correct")
    with pytest.raises(ConnectionError, match="check the passphrase"):
        load_snowflake_private_key(key_path, passphrase="wrong")


def test_load_private_key_missing_passphrase_for_encrypted_key(tmp_path):
    key_path = tmp_path / "rsa_key.p8"
    _write_private_key(key_path, passphrase="correct")
    with pytest.raises(ConnectionError, match="Unable to decrypt"):
        load_snowflake_private_key(key_path, passphrase=None)


def test_load_private_key_path_missing(tmp_path):
    missing = tmp_path / "does-not-exist.p8"
    with pytest.raises(ConnectionError, match="Unable to read"):
        load_snowflake_private_key(missing, passphrase=None)


def test_load_private_key_garbage_file(tmp_path):
    key_path = tmp_path / "not-a-key.p8"
    key_path.write_text("this is not a PEM private key\n")
    with pytest.raises(ConnectionError, match="Invalid Snowflake private key PEM"):
        load_snowflake_private_key(key_path, passphrase=None)


def test_snowflake_key_pair_builds_passwordless_url_with_private_key(tmp_path):
    key_path = tmp_path / "rsa_key.p8"
    _write_private_key(key_path, passphrase=None)

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
