from pathlib import Path
import logging
from typing import Any, Protocol

import yaml

from databricks.labs.lakebridge.connections.env_getter import EnvGetter

logger = logging.getLogger(__name__)


class SecretProvider(Protocol):
    def get_secret(self, key: str) -> str:
        pass


class LocalSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return key


class EnvSecretProvider(SecretProvider):
    def __init__(self, env_getter: EnvGetter):
        self._env_getter = env_getter

    def get_secret(self, key: str) -> str:
        try:
            return self._env_getter.get(str(key))
        except KeyError:
            logger.debug(f"Environment variable {key} not found. Falling back to actual value")
            return key


class DatabricksSecretProvider:
    def get_secret(self, key: str) -> str:
        raise NotImplementedError("Databricks secret vault not implemented")


class CredentialManager:
    def __init__(self, credentials: dict, secret_providers: dict[str, SecretProvider]):
        self._credentials = credentials
        self._default_vault = self._credentials.get('secret_vault_type', 'local').lower()
        self._provider = secret_providers.get(self._default_vault)
        if not self._provider:
            raise ValueError(f"Unsupported secret vault type: {self._default_vault}")

    def get_credentials(self, source: str) -> dict[str, Any]:
        if source not in self._credentials:
            raise KeyError(f"Source system: {source} credentials not found")

        value = self._credentials[source]
        if not isinstance(value, dict):
            raise KeyError(f"Invalid credential format for source: {source}")

        # Safe to cast: we verified value is a dict, so _resolve_credentials returns a dict
        return self._resolve_credentials(value)

    def _resolve_credentials(self, value: dict[str, Any]) -> dict[str, Any]:
        """Recursively resolve credentials, handling nested dictionaries and secret values.

        rules:
        - dict: Recursively process each key-value pair
        - list: Recursively process each item
        - str: Apply secret provider (resolve from env vars or return as-is)
        - Other types (int, bool, None, float): Return unchanged

            Processed value with the same structure but string values resolved
        """
        if isinstance(value, dict):
            return {k: self._resolve_credentials(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_credentials(item) for item in value]
        if isinstance(value, str):
            return self._get_secret_value(value)
        # For int, bool, None, float, etc., return as-is
        return value

    def _get_secret_value(self, key: str) -> str:
        """Apply the configured secret provider to resolve a string value."""
        assert self._provider is not None
        return self._provider.get_secret(key)


def _get_home() -> Path:
    return Path.home()


def cred_file(product_name) -> Path:
    return _get_home() / ".databricks" / "labs" / product_name / ".credentials.yml"


def _load_credentials(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Credentials file not found at {path}") from e


def create_credential_manager(
    product_name: str,
    env_getter: EnvGetter,
    creds_path: Path | None = None,
) -> CredentialManager:
    if creds_path is None:
        creds_path = cred_file(product_name)
    creds = _load_credentials(creds_path)

    secret_providers = {
        'local': LocalSecretProvider(),
        'env': EnvSecretProvider(env_getter),
        'databricks': DatabricksSecretProvider(),
    }

    return CredentialManager(creds, secret_providers)
