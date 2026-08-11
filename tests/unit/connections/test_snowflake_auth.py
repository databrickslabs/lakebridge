from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from databricks.labs.lakebridge.connections.snowflake_auth import (
    AUTH_CHOICES,
    KeyPair,
    Pat,
    resolve_snowflake_credentials,
)


def _write_unencrypted_private_key(path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def test_pat_returns_password_from_config() -> None:
    resolved = Pat.resolve_credentials({"pat": "token-value"})
    assert resolved.password == "token-value"
    assert resolved.private_key is None


def test_pat_missing_raises_connection_error() -> None:
    with pytest.raises(ConnectionError, match="pat"):
        Pat.resolve_credentials({})


def test_pat_empty_raises_connection_error() -> None:
    with pytest.raises(ConnectionError, match="pat"):
        Pat.resolve_credentials({"pat": ""})


def test_resolve_defaults_to_pat_when_auth_type_missing() -> None:
    resolved = resolve_snowflake_credentials({"pat": "token-value"})
    assert resolved.password == "token-value"
    assert resolved.private_key is None


def test_key_pair_returns_private_key_bytes(tmp_path) -> None:
    key_path = tmp_path / "rsa_key.p8"
    _write_unencrypted_private_key(key_path)
    resolved = KeyPair.resolve_credentials({"private_key_path": str(key_path)})
    assert resolved.password is None
    assert isinstance(resolved.private_key, bytes)
    assert resolved.private_key


def test_key_pair_missing_path_raises_connection_error() -> None:
    with pytest.raises(ConnectionError, match="private_key_path"):
        KeyPair.resolve_credentials({})


def test_resolve_key_pair_via_auth_type(tmp_path) -> None:
    key_path = tmp_path / "rsa_key.p8"
    _write_unencrypted_private_key(key_path)
    resolved = resolve_snowflake_credentials(
        {"auth_type": "key_pair", "private_key_path": str(key_path)}
    )
    assert resolved.password is None
    assert resolved.private_key


def test_resolve_unknown_auth_type_raises() -> None:
    with pytest.raises(ConnectionError, match="Invalid Snowflake auth_type"):
        resolve_snowflake_credentials({"auth_type": "oauth"})


def test_auth_choices_expose_snake_case_types_and_labels() -> None:
    assert [cls.auth_type for cls in AUTH_CHOICES] == ["pat", "key_pair"]
    assert Pat.label == "PAT"
    assert KeyPair.label == "Key-Pair"
