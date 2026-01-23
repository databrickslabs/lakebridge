import dataclasses
import logging
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

logger = logging.getLogger(__name__)


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
        "synapse": SynapseConnector,
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


class SynapseConnector(MSSQLConnector):
    """
    Azure Synapse SQL Pool connector.

    This is an adapter that translates Synapse-specific configuration
    to MSSQL connection format, then delegates to MSSQLConnector.

    Synapse SQL pools use the same protocol as SQL Server, so this
    connector inherits from MSSQLConnector and only transforms the config.
    """

    def __init__(self, config: dict[str, Any]):
        # Synapse config may have endpoint_key for dedicated/serverless pools
        # Transform to MSSQL-compatible config
        endpoint_key = config.get('endpoint_key', 'dedicated_sql_endpoint')

        # Build MSSQL-compatible configuration
        mssql_config = {
            'driver': config['driver'],
            'server': config.get('server') or config.get(endpoint_key, config.get('dedicated_sql_endpoint')),
            'database': config['database'],
            'user': config.get('user') or config.get('sql_user'),
            'password': config.get('password') or config.get('sql_password'),
            'port': config.get('port', 1433),
            'auth_type': config.get('auth_type', 'sql_authentication'),
        }

        # Initialize parent with transformed config
        # This will call MSSQLConnector.__init__ which calls _connect()
        super().__init__(mssql_config)


class DatabaseManager:
    def __init__(self, db_type: str, config: dict[str, Any]):
        self.connector = _create_connector(db_type, config)

    def fetch(self, query: str) -> FetchResult:
        try:
            return self.connector.fetch(query)
        except OperationalError as e:
            error_msg = f"Error connecting to the database: {e}"
            logger.error(error_msg)
            raise ConnectionError(error_msg) from e

    def check_connection(self) -> bool:
        query = "SELECT 101 AS test_column"
        result = self.fetch(query)
        if result is None:
            return False
        return result.rows[0][0] == 101
