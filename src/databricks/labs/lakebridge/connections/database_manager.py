import logging
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, Result, URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)
logger.setLevel("INFO")


class DatabaseConnector(ABC):
    @abstractmethod
    def _connect(self) -> Engine:
        pass

    @abstractmethod
    def execute_query(self, query: str) -> Result[Any]:
        pass


class _BaseConnector(DatabaseConnector):
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.engine: Engine = self._connect()

    def _connect(self) -> Engine:
        raise NotImplementedError("Subclasses should implement this method")

    def execute_query(self, query: str) -> Result[Any]:
        if not self.engine:
            raise ConnectionError("Not connected to the database.")
        session = sessionmaker(bind=self.engine)
        connection = session()
        return connection.execute(text(query))


def _create_connector(db_type: str, config: dict[str, Any]) -> DatabaseConnector:
    connectors = {
        "snowflake": SnowflakeConnector,
        "mssql": MSSQLConnector,
        "tsql": MSSQLConnector,
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
        query_params = {"driver": self.config['driver']}

        for key, value in self.config.items():
            if key not in ["user", "password", "server", "database", "port"]:
                query_params[key] = value
        connection_string = URL.create(
            "mssql+pyodbc",
            username=self.config['user'],
            password=self.config['password'],
            host=self.config['server'],
            port=self.config.get('port', 1433),
            database=self.config['database'],
            query=query_params,
        )
        return create_engine(connection_string)


class DatabaseManager:
    def __init__(self, db_type: str, config: dict[str, Any]):
        self.connector = _create_connector(db_type, config)

    def execute_query(self, query: str) -> Result[Any]:
        try:
            return self.connector.execute_query(query)
        except OperationalError:
            logger.error("Error connecting to the database check credentials")
            raise ConnectionError("Error connecting to the database check credentials") from None

    def check_connection(self) -> bool:
        query = "SELECT 101 AS test_column"
        result = self.execute_query(query)
        row = result.fetchone()
        if row is None:
            return False
        return row[0] == 101
