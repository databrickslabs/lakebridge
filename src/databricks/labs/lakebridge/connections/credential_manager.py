from collections.abc import Callable
from functools import partial
from pathlib import Path
import logging
from typing import Protocol
import base64

import yaml

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound

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


class DatabricksSecretProvider(SecretProvider):
    def __init__(self, ws: WorkspaceClient):
        self._ws = ws

    def get_databricks_secret(self, scope: str, key: str) -> str:
        return self.get_secret(f"{scope}/{key}")

    def get_secret(self, key: str) -> str:
        """Get the secret value given a secret scope & secret key.

        :param key: key in the format 'scope/secret'
        :return: The decoded UTF-8 secret value.

        Raises:
          NotFound: The secret could not be found.
          UnicodeDecodeError: The secret value was not Base64-encoded UTF-8.
        """
        key_parts = key.split(sep="/")
        assert len(key_parts) == 2, "Secret name must be in the format 'scope/secret'"
        scope, key_only = key_parts[0], key_parts[1]

        try:
            secret = self._ws.secrets.get_secret(scope, key_only)
            assert secret.value is not None
            return base64.b64decode(secret.value).decode("utf-8")
        except NotFound as e:
            raise KeyError(f'Secret does not exist with scope: {scope} and key: {key_only} : {e}') from e
        except UnicodeDecodeError as e:
            raise UnicodeDecodeError(
                "utf-8",
                key_only.encode(),
                0,
                1,
                f"Secret {key} has Base64 bytes that cannot be decoded to utf-8 string: {e}.",
            ) from e


class CredentialManager:
    SecretProviderFactory = Callable[[], SecretProvider]

    def __init__(self, credentials: dict, secret_providers: dict[str, SecretProviderFactory]):
        self._credentials = credentials
        self._default_vault = self._credentials.get('secret_vault_type', 'local').lower()
        provider_factory = secret_providers.get(self._default_vault)
        if not provider_factory:
            raise ValueError(f"Unsupported secret vault type: {self._default_vault}")
        self._provider = provider_factory()

    def get_credentials(self, source: str) -> dict:
        if source not in self._credentials:
            raise KeyError(f"Source system: {source} credentials not found")

        value = self._credentials[source]
        if not isinstance(value, dict):
            raise KeyError(f"Invalid credential format for source: {source}")

        return {k: self._get_secret_value(v) for k, v in value.items()}

    def _get_secret_value(self, key: str) -> str:
        assert self._provider is not None
        return self._provider.get_secret(key)


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


def create_databricks_secret_provider(ws) -> DatabricksSecretProvider:
    return DatabricksSecretProvider(ws)


def create_credential_manager(creds_or_path: dict | Path, ws: WorkspaceClient | None = None) -> CredentialManager:
    if isinstance(creds_or_path, Path):
        creds = _load_credentials(creds_or_path)
    else:
        creds = creds_or_path

    # Lazily initialize secret providers
    secret_providers: dict[str, CredentialManager.SecretProviderFactory] = {
        'local': LocalSecretProvider,
        'env': partial(EnvSecretProvider, EnvGetter()),
    }

    if ws:
        secret_providers['databricks'] = partial(create_databricks_secret_provider, ws)

    return CredentialManager(creds, secret_providers)
