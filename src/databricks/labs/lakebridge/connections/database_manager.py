# Databricks notebook source
import dataclasses
import json
import logging
import os
from urllib.parse import quote_plus
from abc import ABC, abstractmethod
from typing import Any
from collections.abc import Sequence, Set

import pandas as pd

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL
from sqlalchemy.engine.row import Row
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.session import Session
from sqlalchemy.dialects import registry
from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2

logger = logging.getLogger(__name__)


class RedshiftDialect_psycopg2(PGDialect_psycopg2):
    """Use PostgreSQL dialect but skip standard_conforming_strings (not supported by Redshift)."""
    supports_statement_cache = True

    def _set_backslash_escapes(self, connection):
        self._backslash_escapes = False


@dataclasses.dataclass
class FetchResult:
    columns: Set[str]
    rows: Sequence[Row[Any]]

    def to_df(self) -> pd.DataFrame:
        """Create a pandas dataframe based on these results."""
        # Row emulates a named tuple, which Pandas understands natively. So the columns are safely inferred unless
        # we have an empty result-set.
        return pd.DataFrame(data=self.rows) if self.rows else pd.DataFrame(columns=list(self.columns))


class DatabaseConnector(ABC):
    @abstractmethod
    def _connect(self) -> Engine:
        pass

    @abstractmethod
    def fetch(self, query: str) -> FetchResult:
        pass


class _BaseConnector(DatabaseConnector):
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.engine: Engine = self._connect()

    def _connect(self) -> Engine:
        raise NotImplementedError("Subclasses should implement this method")

    def fetch(self, query: str) -> FetchResult:
        if not self.engine:
            raise ConnectionError("Not connected to the database.")

        with Session(self.engine) as session, session.begin():
            result = session.execute(text(query))
            return FetchResult(result.keys(), result.fetchall())


def _create_connector(db_type: str, config: dict[str, Any]) -> DatabaseConnector:
    connectors = {
        "snowflake": SnowflakeConnector,
        "mssql": MSSQLConnector,
        "tsql": MSSQLConnector,
        "redshift": RedshiftConnector
    }

    connector_class = connectors.get(db_type.lower())

    if connector_class is None:
        raise ValueError(f"Unsupported database type: {db_type}")

    return connector_class(config)


class SnowflakeConnector(_BaseConnector):
    def _connect(self) -> Engine:
        raise NotImplementedError("Snowflake connector not implemented")


class MSSQLConnector(_BaseConnector):
    def _connect(self) -> Engine:
        auth_type = self.config.get('auth_type', 'sql_authentication')
        db_name = self.config.get('database')

        query_params = {
            "driver": self.config['driver'],
            "loginTimeout": "30",
        }

        if auth_type == "ad_passwd_authentication":
            query_params = {
                **query_params,
                "authentication": "ActiveDirectoryPassword",
            }
        elif auth_type == "spn_authentication":
            raise NotImplementedError("SPN Authentication not implemented yet")

        connection_string = URL.create(
            drivername="mssql+pyodbc",
            username=self.config['user'],
            password=self.config['password'],
            host=self.config['server'],
            port=self.config.get('port', 1433),
            database=db_name,
            query=query_params,
        )
        return create_engine(connection_string)


def _get_redshift_federated_credentials(config: dict[str, Any]) -> tuple[str, str]:
    """Resolve Redshift user and password via GetClusterCredentials for federated_user auth.
    Uses get_credentials_db_user (default awsuser) so temp creds are for an existing DB user;
    your federated identity (AWS profile/SSO) authorizes the call."""
    try:
        import boto3
    except ImportError as e:
        raise ConnectionError(
            "federated_user auth requires boto3. Install with: pip install boto3"
        ) from e
    host = config.get("host") or ""
    host_parts = host.split(".")
    cluster_identifier = config.get("cluster_identifier") or (host_parts[0] if host_parts else "")
    db_name = config.get("database") or ""
    db_user = config.get("get_credentials_db_user") or config.get("master_username") or "awsuser"
    region = config.get("region") or (host_parts[2] if len(host_parts) >= 3 else None) or os.environ.get("AWS_REGION", "us-west-2")
    profile = config.get("aws_profile") or os.environ.get("AWS_PROFILE")
    if not cluster_identifier or not db_name:
        raise ConnectionError(
            "federated_user auth requires cluster_identifier (or host) and database in config."
        )
    session_kw: dict[str, Any] = {"region_name": region}
    if profile:
        session_kw["profile_name"] = profile
    session = boto3.Session(**session_kw)
    client = session.client("redshift")
    resp = client.get_cluster_credentials(
        ClusterIdentifier=cluster_identifier,
        DbName=db_name,
        DbUser=db_user,
    )
    return (resp["DbUser"], resp["DbPassword"])


def _get_redshift_secrets_manager_credentials(config: dict[str, Any]) -> dict[str, Any]:
    """Fetch Redshift connection info from AWS Secrets Manager. Secret JSON: username, password, host, port, dbname, engine."""
    try:
        import boto3
    except ImportError as e:
        raise ConnectionError(
            "secrets_manager auth requires boto3. Install with: pip install boto3"
        ) from e
    secret_arn = (config.get("secrets_manager_secret_arn") or "").strip()
    if not secret_arn:
        raise ConnectionError("secrets_manager auth requires secrets_manager_secret_arn in config.")
    profile = config.get("aws_profile") or os.environ.get("AWS_PROFILE")
    arn_parts = secret_arn.split(":")
    region = config.get("region") or (arn_parts[3] if len(arn_parts) >= 4 else None) or os.environ.get("AWS_REGION", "us-west-2")
    session_kw: dict[str, Any] = {"region_name": region}
    if profile:
        session_kw["profile_name"] = profile
    session = boto3.Session(**session_kw)
    client = session.client("secretsmanager")
    resp = client.get_secret_value(SecretId=secret_arn)
    try:
        data = json.loads(resp["SecretString"])
    except (KeyError, json.JSONDecodeError) as e:
        raise ConnectionError(f"secrets_manager: invalid secret format: {e}") from e
    return {
        "host": data.get("host", ""),
        "port": int(data.get("port", 5439)),
        "database": data.get("dbname", ""),
        "user": data.get("username", ""),
        "password": data.get("password", ""),
    }


class RedshiftConnector(_BaseConnector):
    def _connect(self) -> Engine:
        registry.register("redshift_psycopg2", __name__, "RedshiftDialect_psycopg2")
        use_ssl = str(self.config.get("ssl") or "no").lower() in ("yes", "true", "1")
        connect_args = {"sslmode": "require"} if use_ssl else {}
        auth = (self.config.get("auth_method") or "").lower()

        if auth == "secrets_manager":
            sm = _get_redshift_secrets_manager_credentials(self.config)
            host, port, db_name = sm["host"], sm["port"], sm["database"]
            user_enc = quote_plus(sm["user"])
            password_enc = quote_plus(sm["password"])
            url_str = f"redshift_psycopg2://{user_enc}:{password_enc}@{host}:{port}/{db_name}"
            return create_engine(url_str, connect_args=connect_args)
        if auth in ("federated_user", "temporary_credentials_iam"):
            host = self.config["host"]
            port = self.config.get("port", 5439)
            db_name = self.config.get("database") or ""
            user, password = _get_redshift_federated_credentials(self.config)
            user_enc = quote_plus(user)
            password_enc = quote_plus(password)
            url_str = f"redshift_psycopg2://{user_enc}:{password_enc}@{host}:{port}/{db_name}"
            return create_engine(url_str, connect_args=connect_args)
        host = self.config["host"]
        port = self.config.get("port", 5439)
        db_name = self.config.get("database") or ""
        connection_string = URL.create(
            drivername="redshift_psycopg2",
            username=self.config["user"],
            password=self.config["password"],
            host=host,
            port=port,
            database=db_name,
        )
        return create_engine(connection_string, connect_args=connect_args)

class DatabaseManager:
    def __init__(self, db_type: str, config: dict[str, Any]):
        self.connector = _create_connector(db_type, config)

    def fetch(self, query: str) -> FetchResult:
        try:
            return self.connector.fetch(query)
        except OperationalError as e:
            logger.error("Error connecting to the database: %s", e)
            raise ConnectionError(f"Error connecting to the database check credentials: {e}") from e

    def check_connection(self) -> bool:
        query = "SELECT 101 AS test_column"
        result = self.fetch(query)
        if result is None:
            return False
        return result.rows[0][0] == 101