import logging
from dataclasses import dataclass

from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.connections.credential_manager import build_credentials, CredentialManager

logger = logging.getLogger(__name__)


@dataclass
class ReconcileCredentialsConfig:
    vault_type: str
    vault_secret_names: dict[str, str]

    def __post_init__(self):
        if self.vault_type != "databricks":
            raise ValueError(f"Unsupported vault_type: {self.vault_type}")


_REQUIRED_JDBC_CREDS = [
    "host",
    "port",
    "database",
    "user",
    "password",
]

_TSQL_REQUIRED_CREDS = [*_REQUIRED_JDBC_CREDS, "encrypt", "trustServerCertificate"]

_ORACLE_REQUIRED_CREDS = [*_REQUIRED_JDBC_CREDS]

_SNOWFLAKE_REQUIRED_CREDS = [
    "sfUser",
    "sfUrl",
    "sfDatabase",
    "sfSchema",
    "sfWarehouse",
    "sfRole",
    # sfPassword is not required here; auth is validated separately
]

_SOURCE_CREDENTIALS_MAP = {
    "databricks": [],
    "snowflake": _SNOWFLAKE_REQUIRED_CREDS,
    "oracle": _ORACLE_REQUIRED_CREDS,
    "tsql": _TSQL_REQUIRED_CREDS,
    "synapse": _TSQL_REQUIRED_CREDS,
}


def build_source_creds(source: str, secret_scope: str) -> dict:
    keys = _SOURCE_CREDENTIALS_MAP.get(source)
    if not keys:
        raise ValueError(f"Unsupported source system: {source}")
    parsed = {key: f"{secret_scope}/{key}" for key in keys}
    if source == "snowflake":
        logger.warning("Please specify the Snowflake authentication method in the credentials config.")
        parsed["pem_private_key"] = f"{secret_scope}/pem_private_key"
        parsed["sfPassword"] = f"{secret_scope}/sfPassword"
    return parsed


def validate_creds(creds: ReconcileCredentialsConfig, source: str) -> None:
    required_keys = _SOURCE_CREDENTIALS_MAP.get(source)
    if not required_keys:
        raise ValueError(f"Unsupported source system: {source}")

    missing = [k for k in required_keys if not creds.vault_secret_names.get(k)]
    if missing:
        raise ValueError(
            f"Missing mandatory {source} credentials. " f"Please configure all of {required_keys}. Missing: {missing}"
        )


def load_and_validate_credentials(
    creds: ReconcileCredentialsConfig,
    ws: WorkspaceClient,
    source: str,
) -> dict[str, str]:
    validate_creds(creds, source)

    parsed = build_credentials(creds.vault_type, source, creds.vault_secret_names)
    resolved = CredentialManager.from_credentials(parsed, ws).get_credentials(source)
    return resolved
