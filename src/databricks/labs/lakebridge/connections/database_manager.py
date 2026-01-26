import dataclasses
import logging
from abc import ABC, abstractmethod
from typing import Any
from collections.abc import Sequence, Set

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
        auth_type = self.config.get('auth_type', 'sql_authentication').strip()

        query_params = {
            "driver": self.config['driver'].strip(),
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
            username=self.config['user'].strip(),
            password=self.config['password'].strip(),
            host=self.config['server'].strip(),
            port=self.config.get('port', 1433),
            database=self.config['database'].strip(),
            query=query_params,
        )
        
        try:
            logger.info(f"Attempting to connect to database: {self.config['database'].strip()} "
                       f"on server: {self.config['server'].strip()}:{self.config.get('port', 1433)} "
                       f"with driver: {self.config['driver'].strip()}")
            engine = create_engine(connection_string)
            # Test the connection immediately
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection established successfully")
            return engine
        except Exception as e:
            logger.error(f"Failed to create database engine: {type(e).__name__}: {str(e)}")
            raise


class DatabaseManager:
    def __init__(self, db_type: str, config: dict[str, Any]):
        self.connector = _create_connector(db_type, config)

    def fetch(self, query: str) -> FetchResult:
        try:
            return self.connector.fetch(query)
        except OperationalError as e:
            # Log detailed error information for diagnostics
            logger.error(f"Database connection error details: {type(e).__name__}: {str(e)}")
            if hasattr(e, 'orig') and e.orig:
                logger.error(f"Original error: {type(e.orig).__name__}: {str(e.orig)}")
            logger.error(f"Connection parameters (masked): driver={self.connector.config.get('driver', 'N/A')}, "
                        f"server={self.connector.config.get('server', 'N/A')}, "
                        f"database={self.connector.config.get('database', 'N/A')}, "
                        f"port={self.connector.config.get('port', 'N/A')}, "
                        f"auth_type={self.connector.config.get('auth_type', 'N/A')}")
            logger.error("Error connecting to the database check credentials")
            raise ConnectionError("Error connecting to the database check credentials") from None

    def check_connection(self) -> bool:
        query = "SELECT 101 AS test_column"
        result = self.fetch(query)
        if result is None:
            return False
        return result.rows[0][0] == 101
