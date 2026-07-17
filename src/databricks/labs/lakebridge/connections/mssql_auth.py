"""Authentication strategies for MSSQL-family connections (mssql-python driver).

Each class is named after the `Authentication=` connection-string literal it maps to
(e.g. `ActiveDirectoryServicePrincipal`).

The single point of contract is `MSSQLAuth.resolve_credentials(config)` — each class
returns a fully-resolved `ResolvedCredentials` that the connector applies verbatim
to the connection string. No conventions, no class attributes acting as switches.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from databricks.labs.blueprint.installation import JsonObject


@dataclass(frozen=True)
class ResolvedCredentials:
    """Everything `MSSQLConnector` needs to open a connection.

    - `authentication_param`: value of the `Authentication=` connection-string
      keyword; None uses SQL authentication
    - `username` / `password`: emitted as `UID=` / `PWD=` when non-None.
    """

    authentication_param: str | None = None
    username: str | None = None
    password: str | None = None


class MSSQLAuth(Protocol):
    """Authentication strategy. Single classmethod takes a config dict and returns resolved credentials."""

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedCredentials: ...


def _require_user_password(auth_name: str, config: JsonObject) -> tuple[str, str]:
    if not config.get("user") or not config.get("password"):
        raise KeyError(f"{auth_name} requires non-empty 'user' and 'password' in config")
    return str(config["user"]), str(config["password"])


class SqlPassword:
    """SQL Authentication — username and password from the credentials file."""

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedCredentials:
        user, password = _require_user_password(cls.__name__, config)
        return ResolvedCredentials(
            username=user,
            password=password,
        )


class ActiveDirectoryPassword:
    """Entra ID (Azure AD) username + password. Not MFA-capable."""

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedCredentials:
        user, password = _require_user_password(cls.__name__, config)
        return ResolvedCredentials(
            authentication_param="ActiveDirectoryPassword",
            username=user,
            password=password,
        )


class ActiveDirectoryServicePrincipal:
    """Service Principal — credentials sourced from AZURE_CLIENT_ID / AZURE_CLIENT_SECRET env vars."""

    _CLIENT_ID_VAR = "AZURE_CLIENT_ID"
    _CLIENT_SECRET_VAR = "AZURE_CLIENT_SECRET"

    @classmethod
    def resolve_credentials(cls, _config: JsonObject) -> ResolvedCredentials:
        missing = [v for v in (cls._CLIENT_ID_VAR, cls._CLIENT_SECRET_VAR) if not os.environ.get(v)]
        if missing:
            raise OSError(f"ActiveDirectoryServicePrincipal requires env vars: {', '.join(missing)}")
        return ResolvedCredentials(
            authentication_param="ActiveDirectoryServicePrincipal",
            username=os.environ[cls._CLIENT_ID_VAR],
            password=os.environ[cls._CLIENT_SECRET_VAR],
        )


class ActiveDirectoryDefault:
    """Entra ID via the driver's `Authentication=ActiveDirectoryDefault` mode.

    mssql-python resolves the identity internally with the Azure SDK
    `DefaultAzureCredential` chain — SPN env vars (`AZURE_TENANT_ID` /
    `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`), managed identity, then `az login`
    — so no credentials appear in the connection string or on disk. MFA-capable:
    the interactive step, if any, happens in `az login` before the profiler runs.
    """

    @classmethod
    def resolve_credentials(cls, _config: JsonObject) -> ResolvedCredentials:
        return ResolvedCredentials(authentication_param="ActiveDirectoryDefault")


# User-selectable auth methods, in the order they appear in the configurator prompt.
AUTH_CHOICES: list[type[MSSQLAuth]] = [
    SqlPassword,
    ActiveDirectoryDefault,
    ActiveDirectoryPassword,
    ActiveDirectoryServicePrincipal,
]

_AUTH_REGISTRY: dict[str, type[MSSQLAuth]] = {cls.__name__: cls for cls in AUTH_CHOICES}


def resolve_mssql_credentials(config: JsonObject) -> ResolvedCredentials:
    """Look up the configured `auth_type`, dispatch to the matching class, return resolved credentials."""
    auth_type = str(config.get("auth_type", SqlPassword.__name__))
    cls = _AUTH_REGISTRY.get(auth_type)
    if cls is None:
        raise ConnectionError(f"Invalid MSSQL auth_type: {auth_type}")
    return cls.resolve_credentials(config)
