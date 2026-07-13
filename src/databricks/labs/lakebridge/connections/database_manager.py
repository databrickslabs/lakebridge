import contextlib
import dataclasses
import importlib
import logging
import re
from abc import abstractmethod
from types import TracebackType
from collections.abc import Callable, Sequence, Set
from typing import Any, NoReturn

import pandas as pd

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL
from sqlalchemy import text
from sqlalchemy.orm.session import Session
import redshift_connector  # type: ignore[import-untyped]

from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.assessments.errors import (
    ErrorCategory,
    SourceFailure,
    SourceQueryError,
    classify_standard_sqlstate,
)
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
    columns: Set[str]
    rows: Sequence[Sequence[Any]]

    def to_df(self) -> pd.DataFrame:
        """Create a pandas dataframe based on these results."""
        # Row emulates a named tuple, which Pandas understands natively. So the columns are safely inferred unless
        # we have an empty result-set.
        return pd.DataFrame(data=self.rows) if self.rows else pd.DataFrame(columns=list(self.columns))


def _concise_error_message(exc: Exception) -> str:
    return str(getattr(exc, "orig", exc)).split("\n", 1)[0].strip()


_SQLSTATE_PATTERN = re.compile(r"[0-9A-Za-z]{5}")
_SQLSTATE_IN_MESSAGE = re.compile(r"\[SQLState ([0-9A-Za-z]{5})\]")
_TERADATA_ERROR_CODE_PATTERN = re.compile(r"\[Error (\d+)\]")
_TERADATA_ABSENCE_CODES = frozenset({"3802", "3807"})
_TERADATA_PERMISSION_CODES = frozenset({"3523"})


def _sqlstate_from_sqlalchemy_orig(orig: object) -> str | None:
    for attr in ("sqlstate", "pgcode"):
        value = getattr(orig, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


class DatabaseConnector(contextlib.AbstractContextManager):
    @abstractmethod
    def fetch(self, query: str) -> FetchResult:
        pass

    @abstractmethod
    def close(self) -> None:
        pass

    @abstractmethod
    def health_check(self) -> bool:
        pass

    @abstractmethod
    def parse_source_error(self, exc: Exception) -> SourceFailure:
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
            result = session.execute(text(query))
            return FetchResult(result.keys(), result.fetchall())

    def health_check(self) -> bool:
        query = "SELECT 101 AS test_column"
        result = self.fetch(query)
        return result.rows[0][0] == 101

    def _extract_sqlstate(self, exc: Exception) -> str | None:
        orig = getattr(exc, "orig", None)
        if orig is not None:
            return _sqlstate_from_sqlalchemy_orig(orig)
        return None

    def parse_source_error(self, exc: Exception) -> SourceFailure:
        reason = _concise_error_message(exc)
        sqlstate = self._extract_sqlstate(exc)
        category = classify_standard_sqlstate(sqlstate) or ErrorCategory.UNKNOWN
        return SourceFailure(category=category, reason=reason, sqlstate=sqlstate)


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


class MSSQLConnector(_BaseConnector):
    def _connect(self) -> Engine:
        auth_type = self.config.get('auth_type', 'sql_authentication')
        db_value = self.config.get('database')
        db_name = str(db_value) if db_value else None

        query_params: dict[str, str] = {
            "driver": str(self.config['driver']),
            "loginTimeout": "30",
            "TrustServerCertificate": (
                "no" if str(self.config.get('trust_server_certificate', 'False')) == 'False' else "yes"
            ),
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

    def _extract_sqlstate(self, exc: Exception) -> str | None:
        orig = getattr(exc, "orig", None)
        if orig is not None:
            orig_args = getattr(orig, "args", ())
            if orig_args and isinstance(orig_args[0], str) and _SQLSTATE_PATTERN.fullmatch(orig_args[0]):
                return orig_args[0]
        return super()._extract_sqlstate(exc)


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

    def _extract_sqlstate(self, exc: Exception) -> str | None:
        orig = getattr(exc, "orig", None)
        for candidate in (orig, exc) if orig is not None else (exc,):
            match = _SQLSTATE_IN_MESSAGE.search(str(candidate))
            if match:
                return match.group(1)
        return super()._extract_sqlstate(exc)

    @staticmethod
    def _extract_vendor_code(reason: str) -> str | None:
        match = _TERADATA_ERROR_CODE_PATTERN.search(reason)
        return match.group(1) if match else None

    @staticmethod
    def _classify_vendor_code(vendor_code: str | None) -> ErrorCategory | None:
        if vendor_code in _TERADATA_ABSENCE_CODES:
            return ErrorCategory.ABSENCE
        if vendor_code in _TERADATA_PERMISSION_CODES:
            return ErrorCategory.PERMISSION
        return None

    def parse_source_error(self, exc: Exception) -> SourceFailure:
        reason = _concise_error_message(exc)
        sqlstate = self._extract_sqlstate(exc)
        vendor_code = self._extract_vendor_code(reason)
        category = (
            classify_standard_sqlstate(sqlstate) or self._classify_vendor_code(vendor_code) or ErrorCategory.UNKNOWN
        )
        return SourceFailure(category=category, reason=reason, sqlstate=sqlstate, vendor_code=vendor_code)


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
                return FetchResult(set(), [])
            rows = cursor.fetchall()
            columns = {desc[0] for desc in cursor.description} if cursor.description else set()
            return FetchResult(columns, rows)
        finally:
            cursor.close()

    def close(self) -> None:
        self._conn.close()

    def health_check(self) -> bool:
        query = "SELECT 101 AS test_column"
        result = self.fetch(query)
        return result.rows[0][0] == 101

    def parse_source_error(self, exc: Exception) -> SourceFailure:
        reason = _concise_error_message(exc)
        sqlstate = None
        if exc.args and isinstance(exc.args[0], dict):
            code = exc.args[0].get("C")
            if isinstance(code, str) and code:
                sqlstate = code
        category = classify_standard_sqlstate(sqlstate) or ErrorCategory.UNKNOWN
        return SourceFailure(category=category, reason=reason, sqlstate=sqlstate)


def _create_connector(db_type: str, config: JsonObject) -> DatabaseConnector:
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


# TODO remove this class, connectors are managed using ContextManager
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

    def _raise_source_query_error(self, exc: Exception) -> NoReturn:
        raise SourceQueryError(self.connector.parse_source_error(exc)) from exc

    def fetch(self, query: str) -> FetchResult:
        try:
            return self.connector.fetch(query)
        except SourceQueryError:
            raise
        except Exception as e:
            logger.debug("Database query failed", exc_info=True)
            self._raise_source_query_error(e)

    def check_connection(self) -> bool:
        try:
            return self.connector.health_check()
        except SourceQueryError:
            raise
        except Exception as e:
            logger.debug("Database health check failed", exc_info=True)
            self._raise_source_query_error(e)
