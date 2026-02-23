from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol

import boto3  # type: ignore[import-untyped]
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


class AwsSecretsManagerProvider:
    def __init__(
        self,
        region_name: str | None = None,
        profile_name: str | None = None,
        assume_role_arn: str | None = None,
    ):
        self._region_name = region_name
        self._profile_name = profile_name
        self._assume_role_arn = assume_role_arn
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        session = boto3.Session(
            region_name=self._region_name,
            profile_name=self._profile_name,
        )

        if self._assume_role_arn:
            sts = session.client("sts")
            assumed = sts.assume_role(
                RoleArn=self._assume_role_arn,
                RoleSessionName="lakebridge-secrets-manager",
            )
            creds = assumed["Credentials"]
            session = boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
                region_name=self._region_name,
            )

        self._client = session.client("secretsmanager")
        return self._client

    def get_secret(self, key: str) -> str:
        secret_name, _, json_key = key.partition("#")
        response = self._get_client().get_secret_value(SecretId=secret_name)

        if "SecretString" in response:
            secret_value = response["SecretString"]
        else:
            secret_value = response["SecretBinary"].decode("utf-8")

        if json_key:
            parsed = json.loads(secret_value)
            return str(parsed[json_key])

        return secret_value


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

    aws_cfg = creds.get('aws_secrets_manager') or {}
    secret_providers = {
        'local': LocalSecretProvider(),
        'env': EnvSecretProvider(env_getter),
        'databricks': DatabricksSecretProvider(),
        'aws_secrets_manager': AwsSecretsManagerProvider(
            region_name=aws_cfg.get('region_name'),
            profile_name=aws_cfg.get('profile_name'),
            assume_role_arn=aws_cfg.get('assume_role_arn'),
        ),
    }

    return CredentialManager(creds, secret_providers)
