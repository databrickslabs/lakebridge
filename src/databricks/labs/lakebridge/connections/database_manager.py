# Databricks notebook source
import contextlib
import dataclasses
import logging
from abc import abstractmethod
from types import TracebackType
from collections.abc import Callable, Sequence, Set

import pandas as pd

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL
from sqlalchemy.engine.row import Row
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.session import Session
import redshift_connector  # type: ignore[import-untyped]
from redshift_connector import Connection as RedshiftConnection  # type: ignore[import-untyped]

from databricks.labs.blueprint.installation import JsonObject

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class FetchResult:
    columns: Set[str]
    rows: Sequence[Row[tuple[object, ...]] | tuple[object, ...]]

    def to_df(self) -> pd.DataFrame:
        """Create a pandas dataframe based on these results."""
        # Row emulates a named tuple, which Pandas understands natively. So the columns are safely inferred unless
        # we have an empty result-set.
        return pd.DataFrame(data=self.rows) if self.rows else pd.DataFrame(columns=list(self.columns))


class DatabaseConnector(contextlib.AbstractContextManager):
    @abstractmethod
    def _connect(self) -> Engine | RedshiftConnection:
        pass

    @abstractmethod
    def fetch(self, query: str) -> FetchResult:
        pass

    @abstractmethod
    def close(self) -> None:
        pass


class _BaseConnector(DatabaseConnector):
    def __init__(self, config: JsonObject):
        self.config = config
        self.engine: Engine = self._connect()

    def _connect(self) -> Engine:
        raise NotImplementedError("Subclasses should implement this method")

    def close(self) -> None:
        self.engine.dispose()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def fetch(self, query: str) -> FetchResult:
        if not self.engine:
            raise ConnectionError("Not connected to the database.")

        with Session(self.engine) as session, session.begin():
            result = session.execute(text(query))
            return FetchResult(result.keys(), result.fetchall())


class SnowflakeConnector(_BaseConnector):
    def _connect(self) -> Engine:
        raise NotImplementedError("Snowflake connector not implemented")


class MSSQLConnector(_BaseConnector):
    def _connect(self) -> Engine:
        auth_type = self.config.get('auth_type', 'sql_authentication')
        db_name = str(self.config.get('database'))

        query_params: dict[str, str] = {
            "driver": str(self.config['driver']),
            "loginTimeout": "30",
        }

        if auth_type == "ad_passwd_authentication":
            query_params = {
                **query_params,
                "authentication": "ActiveDirectoryPassword",
            }
        elif auth_type == "spn_authentication":
            raise NotImplementedError("SPN Authentication not implemented yet")
        elif auth_type == "sql_authentication":
            pass
        else:
            raise ConnectionError(f"Invalid MSSQL auth_type: {auth_type}")

        connection_string = URL.create(
            drivername="mssql+pyodbc",
            username=str(self.config['user']),
            password=str(self.config['password']),
            host=str(self.config['server']),
            port=int(str(self.config.get('port', '1433'))),
            database=db_name,
            query=query_params,
        )
        return create_engine(connection_string)


class RedshiftConnector(DatabaseConnector):
    def __init__(self, config: JsonObject):
        self.config = config
        self._conn: RedshiftConnection = self._connect()

    def _connect(self) -> RedshiftConnection:
        auth_type = str(self.config.get("auth_type", "sql_authentication")).lower()
        connect_kwargs = {
            "host": str(self.config["host"]),
            "database": str(self.config["database"]),
            "port": int(str(self.config.get("port", "5439"))),
            "ssl": str(self.config.get("ssl", "true")).lower() in {"true", "yes", "1"},
        }

        if auth_type == "iam":
            connect_kwargs["iam"] = True
            for key in ("region", "profile", "cluster_identifier", "db_user"):
                if key in self.config:
                    connect_kwargs[key] = str(self.config[key])
        elif auth_type == "secrets_manager":
            raise NotImplementedError("Redshift Secrets Manager authentication not implemented yet")
        elif auth_type == "sql_authentication":
            connect_kwargs["user"] = str(self.config["user"])
            connect_kwargs["password"] = str(self.config["password"])
        else:
            raise ConnectionError(f"Invalid Redshift auth_type: {auth_type}")

        return redshift_connector.connect(**connect_kwargs)

    def fetch(self, query: str) -> FetchResult:
        cursor = self._conn.cursor()
        try:
            cursor.execute(query)
            # DDL (e.g. DROP, CREATE VIEW) has no result set; return empty result
            if cursor.description is None:
                return FetchResult(set(), [])
            rows = cursor.fetchall()
            columns = {desc[0] for desc in cursor.description} if cursor.description else set()
            return FetchResult(columns, rows)
        finally:
            cursor.close()

    def close(self) -> None:
        self._conn.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


def _create_connector(db_type: str, config: JsonObject) -> DatabaseConnector:
    connectors: dict[str, Callable[[JsonObject], DatabaseConnector]] = {
        "snowflake": SnowflakeConnector,
        "mssql": MSSQLConnector,
        "tsql": MSSQLConnector,
        "synapse": MSSQLConnector,  # Synapse uses MSSQL protocol
        "redshift": RedshiftConnector,
    }

    connector_class = connectors.get(db_type.lower())

    if connector_class is None:
        raise ValueError(f"Unsupported database type: {db_type}")

    return connector_class(config)


class DatabaseManager:
    def __init__(self, db_type: str, config: JsonObject):
        self.connector: DatabaseConnector = _create_connector(db_type, config)

    def __enter__(self) -> "DatabaseManager":
        """Support context manager protocol for resource management."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up connector resources when exiting context."""
        self.connector.__exit__(exc_type, exc_val, exc_tb)

    def fetch(self, query: str) -> FetchResult:
        try:
            return self.connector.fetch(query)
        except OperationalError as e:
            logger.exception(f"Error connecting to the database: {e}")
            raise ConnectionError(f"Error connecting to the database check credentials: {e}") from e

    def check_connection(self) -> bool:
        query = "SELECT 101 AS test_column"
        result = self.fetch(query)
        if result is None:
            return False
        return result.rows[0][0] == 101
