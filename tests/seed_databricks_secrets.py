"""Seed Databricks secret scopes used by reconcile integration tests.

Reads connector credentials from environment variables and writes them into
the secret scopes the reconcile connectors look up at test time.

Usage
-----

In CI and locally, the env vars live in ``~/.databricks/debug-env.json``
under the ``ucws`` key. The script falls back to that file automatically
when an env var is missing.

Authentication uses the default ``WorkspaceClient`` config — i.e. the
``DATABRICKS_*`` env vars / ``~/.databrickscfg`` profile already on the host.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors.platform import ResourceAlreadyExists

logger = logging.getLogger("seed_databricks_secrets")

DEBUG_ENV_FILE = Path.home() / ".databricks" / "debug-env.json"
DEBUG_ENV_PROFILE = "ucws"


def _load_env() -> dict[str, str]:
    """Return env vars, layered: process env wins, debug-env.json fills gaps."""
    env = dict(os.environ)
    if DEBUG_ENV_FILE.exists():
        try:
            data = json.loads(DEBUG_ENV_FILE.read_text(encoding="utf-8"))
            for key, value in (data.get(DEBUG_ENV_PROFILE) or {}).items():
                env.setdefault(key, str(value))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"Could not read {DEBUG_ENV_FILE}: {exc}")
    return env


def _snowflake_secrets(env: dict[str, str]) -> dict[str, str] | None:
    """Build the Snowflake secret payload from ``TEST_SNOWFLAKE_*`` env vars.

    Parses ``jdbc:snowflake://account.snowflakecomputing.com/?user=...&db=...&schema=...&warehouse=...``.
    """
    jdbc = env.get("TEST_SNOWFLAKE_JDBC")
    pem = env.get("TEST_SNOWFLAKE_PRIVATE_KEY")
    if not jdbc or not pem:
        return None
    parts = urlparse(jdbc.removeprefix("jdbc:"))
    params = dict(p.split("=", 1) for p in parts.query.split("&") if "=" in p)
    if not parts.hostname:
        raise ValueError(f"Could not parse hostname from TEST_SNOWFLAKE_JDBC: {jdbc!r}")
    url = parts.hostname
    account = url.partition(".snowflakecomputing.com")[0]
    return {
        "sfUrl": url,
        "sfAccount": account,
        "sfUser": params.get("user", ""),
        "sfDatabase": params.get("db", ""),
        "sfSchema": params.get("schema", ""),
        "sfWarehouse": params.get("warehouse", ""),
        "sfRole": "LABS",
        "pem_private_key": pem,
    }


def _tsql_secrets(env: dict[str, str]) -> dict[str, str] | None:
    """Build the SQL Server secret payload from ``TEST_TSQL_*`` env vars.

    Parses ``jdbc:sqlserver://host:port;database=...;encrypt=...;trustServerCertificate=...``.
    """
    jdbc = env.get("TEST_TSQL_JDBC")
    user = env.get("TEST_TSQL_USER")
    password = env.get("TEST_TSQL_PASS")
    if not jdbc or not user or not password:
        return None
    base, _, params = jdbc.removeprefix("jdbc:").partition(";")
    parsed = urlparse(base)
    if not parsed.hostname:
        raise ValueError(f"Could not parse hostname from TEST_TSQL_JDBC: {jdbc!r}")
    out: dict[str, str] = {
        "host": parsed.hostname,
        "port": str(parsed.port or 1433),
        "user": user,
        "password": password,
    }
    for param in params.split(";"):
        if "=" not in param:
            continue
        key, value = param.split("=", 1)
        if key in {"database", "encrypt", "trustServerCertificate"}:
            out[key] = value
    out.setdefault("encrypt", "true")
    out.setdefault("trustServerCertificate", "false")
    return out


def _redshift_secrets(env: dict[str, str]) -> dict[str, str] | None:
    """Build the Redshift secret payload from ``REDSHIFT_*`` env vars."""
    mapping = {
        "host": "REDSHIFT_HOST",
        "port": "REDSHIFT_PORT",
        "database": "REDSHIFT_DATABASE",
        "user": "REDSHIFT_USER",
        "password": "REDSHIFT_PASS",
    }
    out: dict[str, str] = {}
    for secret_key, env_key in mapping.items():
        value = env.get(env_key)
        if value is None:
            return None
        out[secret_key] = value
    return out


def _ensure_scope(ws: WorkspaceClient, scope: str) -> None:
    try:
        ws.secrets.create_scope(scope)
        logger.info(f"Created secret scope {scope}")
    except ResourceAlreadyExists:
        logger.info(f"Secret scope {scope} already exists")


def _put_secrets(ws: WorkspaceClient, scope: str, secrets: dict[str, str]) -> None:
    for key, value in secrets.items():
        ws.secrets.put_secret(scope=scope, key=key, string_value=value)
        logger.info(f"Wrote {scope}/{key}")


def seed() -> int:
    ws = WorkspaceClient()
    env = _load_env()

    scopes = {
        "labs_snowflake_sandbox_secrets": _snowflake_secrets(env),
        "labs_azure_sandbox_sql_server_secrets": _tsql_secrets(env),
        "labs_redshift_sandbox_secrets": _redshift_secrets(env),
    }

    seeded = 0
    for scope, secrets in scopes.items():
        if secrets is None:
            logger.warning(f"Skipping {scope}: required env vars are not set")
            continue
        _ensure_scope(ws, scope)
        _put_secrets(ws, scope, secrets)
        seeded += 1
    return seeded


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    count = seed()
    logger.info(f"Seeded {count} secret scope(s)")
    return 0 if count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
