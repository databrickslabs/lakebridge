from pathlib import Path
import logging
from typing import Protocol

import yaml

from databricks.labs.lakebridge.connections.env_getter import EnvGetter


logger = logging.getLogger(__name__)


class SecretProvider(Protocol):
    def get_secret(self, key: str) -> str:
        pass


class LocalSecretProvider:
    def get_secret(self, key: str) -> str:
        return key


class EnvSecretProvider:
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
    def __init__(self, credential_loader: dict, secret_providers: dict):
        self._credentials = credential_loader
        self._secret_providers = secret_providers
        self._default_vault = self._credentials.get('secret_vault_type', 'local').lower()

    def get_credentials(self, source: str) -> dict:
        if source not in self._credentials:
            raise KeyError(f"Source system: {source} credentials not found")

        value = self._credentials[source]
        if not isinstance(value, dict):
            raise KeyError(f"Invalid credential format for source: {source}")

        return {k: self._get_secret_value(v) for k, v in value.items()}

    def _get_secret_value(self, key: str) -> str:
        provider = self._secret_providers.get(self._default_vault)
        if not provider:
            raise ValueError(f"Unsupported secret vault type: {self._default_vault}")
        return provider.get_secret(key)


def _get_home() -> Path:
    return Path(__file__).home()


def cred_file(product_name) -> Path:
    return Path(f"{_get_home()}/.databricks/labs/{product_name}/.credentials.yml")


def _load_credentials(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Credentials file not found at {path}") from e


def create_credential_manager(product_name: str, env_getter: EnvGetter):
    file_path = Path(f"{_get_home()}/.databricks/labs/{product_name}/.credentials.yml")

    secret_providers = {
        'local': LocalSecretProvider(),
        'env': EnvSecretProvider(env_getter),
        'databricks': DatabricksSecretProvider(),
    }

    loader = _load_credentials(file_path)
    return CredentialManager(loader, secret_providers)
