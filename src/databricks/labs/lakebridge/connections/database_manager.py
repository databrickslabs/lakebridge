import contextlib
import dataclasses
import importlib
import logging
from abc import abstractmethod
from collections.abc import Callable, Sequence
from types import TracebackType
from typing import Any

import pandas as pd

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.session import Session
import mssql_python
import redshift_connector  # type: ignore[import-untyped]

from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.connections.mssql_auth import resolve_mssql_credentials
from databricks.labs.lakebridge.connections.snowflake_utils import (
    parse_snowflake_account,
    is_valid_snowflake_account,
)

# Side-effect import: registers the 'snowflake://' SQLAlchemy dialect so
# `create_engine("snowflake://...")` resolves in SnowflakeConnector below.
# Done via importlib so pylint doesn't flag it as an unused name.
importlib.import_module("snowflake.sqlalchemy")

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class FetchResult:
    columns: Sequence[str]
    rows: Sequence[Sequence[Any]]

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame.from_records(self.rows, columns=list(self.columns))


class DatabaseConnector(contextlib.AbstractContextManager):
    @abstractmethod
    def fetch(self, query: str) -> FetchResult: ...

    @abstractmethod
    def close(self) -> None:
        pass

    @abstractmethod
    def health_check(self) -> bool:
        pass

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


class _BaseConnector(DatabaseConnector):
    def __init__(self, config: JsonObject):
        self.config = config
        self.engine: Engine = self._connect()

    def _connect(self) -> Engine:
        raise NotImplementedError("Subclasses should implement this method")

    def close(self) -> None:
        self.engine.dispose()

    def fetch(self, query: str) -> FetchResult:
        if not self.engine:
            raise ConnectionError("Not connected to the database.")

        with Session(self.engine) as session, session.begin():
            try:
                result = session.execute(text(query))
                return FetchResult(list(result.keys()), result.fetchall())
            except DBAPIError as e:
                logger.debug("Database query failed", exc_info=True)
                reason = str(getattr(e, "orig", e)).split("\n", 1)[0].strip()
                raise ConnectionError(f"Database query failed: {reason}") from e

    def health_check(self) -> bool:
        query = "SELECT 101 AS test_column"
        result = self.fetch(query)
        return result.rows[0][0] == 101


class SnowflakeConnector(_BaseConnector):
    def _connect(self) -> Engine:
        # The configurator always nests Snowflake credentials under a "connection" block.
        # The SDK types JSON values loosely, so narrow to a dict for the accesses below.
        connection_config = self.config["connection"]
        if not isinstance(connection_config, dict):
            raise ConnectionError("Snowflake credentials must be nested under a 'connection' block")

        account = parse_snowflake_account(str(connection_config["account"]))
        if not is_valid_snowflake_account(account):
            raise ConnectionError(
                f"Invalid Snowflake account identifier {account!r}. Expected something like "
                "'myorg-myaccount' or a legacy locator; check for spaces or stray characters."
            )
        user = str(connection_config["user"])
        warehouse = str(connection_config.get("warehouse", "COMPUTE_WH"))
        database = str(connection_config.get("database", "SNOWFLAKE"))
        schema = str(connection_config.get("schema", "ACCOUNT_USAGE"))
        role = str(connection_config.get("role", "ACCOUNTADMIN"))
        password = str(connection_config["pat"])

        # PAT is base64url-encoded and can contain '/', '=', '@'. URL.create
        # percent-escapes them so SQLAlchemy doesn't misread the token as URL structure.
        snowflake_url = URL.create(
            drivername="snowflake",
            username=user,
            password=password,
            host=account,
            database=f"{database}/{schema}",
            query={"warehouse": warehouse, "role": role},
        )
        return create_engine(snowflake_url)


# In the mssql credential's ``database`` field, this sentinel (or a blank/whitespace value) means "no
# specific database": connect to ``master``. The multi-database SQL Server profiler then enumerates all DBs.
ALL_DATABASES = "*"


class MSSQLConnector(DatabaseConnector):
    def __init__(self, config: JsonObject):
        self.config = config
        self._conn: mssql_python.Connection = self._connect()

    def _connect(self) -> mssql_python.Connection:
        db_value = str(self.config.get('database') or "").strip()
        db_name = db_value if db_value and db_value != ALL_DATABASES else "master"

        resolved = resolve_mssql_credentials(self.config)

        server = str(self.config['server'])
        port = int(str(self.config.get('port', '1433')))
        parts = [f"Server={server},{port}"]
        if self.config.get('database'):
            parts.append(f"Database={db_name}")
        if resolved.authentication_param is not None:
            parts.append(f"Authentication={resolved.authentication_param}")
        if resolved.username is not None:
            parts.append(f"UID={resolved.username}")
        if resolved.password is not None:
            parts.append(f"PWD={resolved.password}")
        trust = "no" if str(self.config.get('trust_server_certificate', 'False')) == 'False' else "yes"
        parts.append(f"TrustServerCertificate={trust}")

        try:
            return mssql_python.connect(
                ";".join(parts),
                autocommit=True,
                timeout=int(str(self.config.get('login_timeout', '30'))),
            )
        except mssql_python.Error as e:
            raise ConnectionError(f"Failed to connect to {server}: {e}") from e

    def fetch(self, query: str) -> FetchResult:
        cursor = self._conn.cursor()
        try:
            cursor.execute(query)
            if cursor.description is None:
                return FetchResult([], [])
            names = [desc[0] for desc in cursor.description]
            rows = [tuple(row) for row in cursor.fetchall()]
            return FetchResult(names, rows)
        except mssql_python.Error as e:
            logger.debug("Database query failed", exc_info=True)
            reason = str(e).split("\n", 1)[0].strip()
            raise ConnectionError(f"Database query failed: {reason}") from e
        finally:
            cursor.close()

    def close(self) -> None:
        self._conn.close()

    def health_check(self) -> bool:
        result = self.fetch("SELECT 101 AS test_column")
        return result.rows[0][0] == 101


class TeradataConnector(_BaseConnector):
    def _connect(self) -> Engine:
        query_params: dict[str, str] = {}
        if self.config.get("database"):
            query_params["database"] = str(self.config["database"])

        connection_string = URL.create(
            drivername="teradatasql",
            username=str(self.config['user']),
            password=str(self.config['password']),
            host=str(self.config['host']),
            port=int(str(self.config.get('port', 1025))),
            query=query_params,
        )
        return create_engine(connection_string)


class OracleConnector(_BaseConnector):
    def _connect(self) -> Engine:
        connection_string = URL.create(
            drivername="oracle+oracledb",
            username=str(self.config['user']),
            password=str(self.config['password']),
            host=str(self.config['host']),
            port=int(str(self.config.get('port', 1521))),
            database=str(self.config.get('service_name')),
        )

        return create_engine(connection_string)

    def health_check(self) -> bool:
        query = "SELECT 101 AS test_column FROM dual"
        result = self.fetch(query)
        return result.rows[0][0] == 101


class RedshiftConnector(DatabaseConnector):
    def __init__(self, config: JsonObject):
        self.config = config
        self._conn: redshift_connector.Connection = self._connect()
        # Optional extract steps can fail (e.g. STV on serverless). Without autocommit that
        # aborts the transaction (25P02); rollback would also undo source_ddl query_view.
        self._conn.autocommit = True

    def _connect(self) -> redshift_connector.Connection:
        auth_type = str(self.config.get("auth_type", "sql_authentication")).lower()
        host = str(self.config["host"])
        database = str(self.config["database"])
        port = int(str(self.config.get("port", "5439")))
        ssl = str(self.config.get("ssl", "true")).lower() in {"true", "yes", "1"}

        if auth_type == "sql_authentication":
            return redshift_connector.connect(
                host=host,
                database=database,
                port=port,
                ssl=ssl,
                user=str(self.config["user"]),
                password=str(self.config["password"]),
            )
        if auth_type == "iam":
            return redshift_connector.connect(
                host=host,
                database=database,
                port=port,
                ssl=ssl,
                iam=True,
                region=str(self.config["region"]) if "region" in self.config else None,
                profile=str(self.config["aws_profile"]) if "aws_profile" in self.config else None,
                cluster_identifier=(
                    str(self.config["cluster_identifier"]) if "cluster_identifier" in self.config else None
                ),
                db_user=str(self.config["db_user"]) if "db_user" in self.config else None,
            )
        raise ConnectionError(f"Invalid Redshift auth_type: {auth_type}. Expected one of: sql_authentication, iam")

    def fetch(self, query: str) -> FetchResult:
        cursor = self._conn.cursor()
        try:
            cursor.execute(query)
            # DDL (e.g. DROP, CREATE VIEW) has no result set; return empty result
            if cursor.description is None:
                return FetchResult([], [])
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            return FetchResult(columns, rows)
        except redshift_connector.Error as e:
            logger.debug("Database query failed", exc_info=True)
            reason = str(e).split("\n", 1)[0].strip()
            raise ConnectionError(f"Database query failed: {reason}") from e
        finally:
            cursor.close()

    def close(self) -> None:
        self._conn.close()

    def health_check(self) -> bool:
        query = "SELECT 101 AS test_column"
        result = self.fetch(query)
        return result.rows[0][0] == 101


def create_connector(db_type: str, config: JsonObject) -> DatabaseConnector:
    connectors: dict[str, Callable[[JsonObject], DatabaseConnector]] = {
        "snowflake": SnowflakeConnector,
        "mssql": MSSQLConnector,
        "synapse": MSSQLConnector,  # Synapse uses MSSQL protocol
        "legacy_synapse": MSSQLConnector,
        "redshift": RedshiftConnector,
        "oracle": OracleConnector,
        "teradata": TeradataConnector,
    }

    connector_class = connectors.get(db_type.lower())

    if connector_class is None:
        raise ValueError(f"Unsupported database type: {db_type}")

    return connector_class(config)
