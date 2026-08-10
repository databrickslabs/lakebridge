"""Authentication strategies for Snowflake profiler connections.

Mirrors `mssql_auth.py`: each selectable method is a class, `AUTH_CHOICES` drives the
configurator prompt, and `resolve_snowflake_credentials` is the single dispatch point
the connector calls. Class names are stored as `auth_type` in credentials YAML.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from databricks.labs.blueprint.installation import JsonObject

from databricks.labs.lakebridge.connections.snowflake_utils import load_snowflake_private_key


@dataclass(frozen=True)
class ResolvedSnowflakeCredentials:
    """Everything `SnowflakeConnector` needs after auth resolution.

    - `password`: PAT (or equivalent) placed in the SQLAlchemy URL password field
    - `private_key`: DER PKCS8 bytes for ``connect_args={"private_key": ...}``
    """

    password: str | None = None
    private_key: bytes | None = None


class SnowflakeAuth(Protocol):
    """Authentication strategy. Single classmethod takes a config dict and returns resolved credentials."""

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedSnowflakeCredentials: ...


class Pat:
    """Programmatic Access Token — stored under `pat` and used as the URL password."""

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedSnowflakeCredentials:
        pat = config.get("pat")
        if not pat:
            raise ConnectionError(f"{cls.__name__} requires a non-empty 'pat' in credentials")
        return ResolvedSnowflakeCredentials(password=str(pat))


class KeyPair:
    """RSA key-pair (JWT) — PEM file on disk, passed via SQLAlchemy connect_args."""

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedSnowflakeCredentials:
        key_path_value = config.get("private_key_path")
        if not key_path_value:
            raise ConnectionError(f"{cls.__name__} requires 'private_key_path' in credentials")
        passphrase = config.get("private_key_passphrase")
        passphrase_str = str(passphrase) if passphrase else None
        private_key = load_snowflake_private_key(Path(str(key_path_value)), passphrase_str)
        return ResolvedSnowflakeCredentials(private_key=private_key)


# User-selectable auth methods, in the order they appear in the configurator prompt.
AUTH_CHOICES: list[type[SnowflakeAuth]] = [
    Pat,
    KeyPair,
]

_AUTH_REGISTRY: dict[str, type[SnowflakeAuth]] = {cls.__name__: cls for cls in AUTH_CHOICES}

# Accept snake_case values written by the first draft of this feature, plus missing auth_type.
_AUTH_ALIASES: dict[str, str] = {
    "pat": Pat.__name__,
    "key_pair": KeyPair.__name__,
}


def resolve_snowflake_credentials(config: JsonObject) -> ResolvedSnowflakeCredentials:
    """Look up the configured `auth_type`, dispatch to the matching class, return resolved credentials."""
    raw = config.get("auth_type", Pat.__name__)
    auth_type = _AUTH_ALIASES.get(str(raw).lower(), str(raw))
    cls = _AUTH_REGISTRY.get(auth_type)
    if cls is None:
        expected = ", ".join(c.__name__ for c in AUTH_CHOICES)
        raise ConnectionError(f"Invalid Snowflake auth_type: {raw!r}. Expected one of: {expected}")
    return cls.resolve_credentials(config)
