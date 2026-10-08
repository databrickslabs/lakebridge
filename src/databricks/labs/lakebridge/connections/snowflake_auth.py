"""Authentication strategies for Snowflake profiler connections.

Each selectable method is a class with:
 - ``auth_type``: snake_case identity stored in credentials YAML and used for dispatch
 - ``label``: user-facing string shown in prompts and docs

``AUTH_CHOICES`` drives the configurator prompt; ``resolve_snowflake_credentials`` is the
single dispatch point the connector calls.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from databricks.labs.blueprint.installation import JsonObject

from databricks.labs.lakebridge.connections.snowflake_utils import load_snowflake_private_key


@dataclass(frozen=True)
class ResolvedSnowflakeCredentials:
    """Everything `SnowflakeConnector` needs after auth resolution.

    - `password`: PAT (or equivalent) placed in the SQLAlchemy URL password field
    - `connect_args`: extra kwargs for ``create_engine`` (e.g. ``private_key``)
    """

    password: str | None = None
    connect_args: dict[str, Any] = field(default_factory=dict)


class SnowflakeAuth(ABC):
    """Authentication strategy with a stored identity and a user-facing label."""

    auth_type: ClassVar[str]
    label: ClassVar[str]

    @classmethod
    @abstractmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedSnowflakeCredentials: ...


class Pat(SnowflakeAuth):
    """Programmatic Access Token — stored under `pat` and used as the URL password."""

    auth_type = "pat"
    label = "PAT"

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedSnowflakeCredentials:
        pat = config.get("pat")
        if not pat:
            raise ConnectionError(f"{cls.auth_type} requires a non-empty 'pat' in credentials")
        return ResolvedSnowflakeCredentials(password=str(pat))


class KeyPair(SnowflakeAuth):
    """RSA key-pair — PEM file on disk, passed via SQLAlchemy connect_args."""

    auth_type = "key_pair"
    label = "Key-Pair"

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedSnowflakeCredentials:
        key_path_value = config.get("private_key_path")
        if not key_path_value:
            raise ConnectionError(f"{cls.auth_type} requires 'private_key_path' in credentials")
        passphrase = config.get("private_key_passphrase")
        passphrase_str = str(passphrase) if passphrase else None
        private_key = load_snowflake_private_key(Path(str(key_path_value)), passphrase_str)
        return ResolvedSnowflakeCredentials(connect_args={"private_key": private_key})


# User-selectable auth methods, in the order they appear in the configurator prompt.
AUTH_CHOICES: Sequence[type[SnowflakeAuth]] = (
    Pat,
    KeyPair,
)

_AUTH_REGISTRY: Mapping[str, type[SnowflakeAuth]] = {cls.auth_type: cls for cls in AUTH_CHOICES}


def resolve_snowflake_credentials(config: JsonObject) -> ResolvedSnowflakeCredentials:
    """Look up the configured `auth_type`, dispatch to the matching class, return resolved credentials."""
    raw = config.get("auth_type", Pat.auth_type)
    auth_type = str(raw)
    cls = _AUTH_REGISTRY.get(auth_type)
    if cls is None:
        expected = ", ".join(c.auth_type for c in AUTH_CHOICES)
        raise ConnectionError(f"Invalid Snowflake auth_type: {raw!r}. Expected one of: {expected}")
    return cls.resolve_credentials(config)
