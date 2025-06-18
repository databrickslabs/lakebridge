import logging
from abc import ABC, abstractmethod

from pyspark.sql import DataFrame

from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Schema

logger = logging.getLogger(__name__)


class DataSource(ABC):

    @abstractmethod
    def read_data(
        self,
        catalog: str | None,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        return NotImplemented

    @abstractmethod
    def get_schema(
        self,
        catalog: str | None,
        schema: str,
        table: str,
    ) -> list[Schema]:
        return NotImplemented

    @classmethod
    def log_and_throw_exception(cls, exception: Exception, fetch_type: str, query: str):
        error_msg = f"Runtime exception occurred while fetching {fetch_type} using {query} : {exception}"
        logger.warning(error_msg)
        raise DataSourceRuntimeException(error_msg) from exception


class MockDataSource(DataSource):

    def __init__(
        self,
        dataframe_repository: dict[tuple[str, str, str], DataFrame],
        schema_repository: dict[tuple[str, str, str], list[Schema]],
        exception: Exception = RuntimeError("Mock Exception"),
    ):
        self._dataframe_repository: dict[tuple[str, str, str], DataFrame] = dataframe_repository
        self._schema_repository: dict[tuple[str, str, str], list[Schema]] = schema_repository
        self._exception = exception

    def read_data(
        self,
        catalog: str | None,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        catalog_str = catalog if catalog else ""
        mock_df = self._dataframe_repository.get((catalog_str, schema, query))
        if not mock_df:
            return self.log_and_throw_exception(self._exception, "data", f"({catalog}, {schema}, {query})")
        return mock_df

    def get_schema(self, catalog: str | None, schema: str, table: str) -> list[Schema]:
        catalog_str = catalog if catalog else ""
        mock_schema = self._schema_repository.get((catalog_str, schema, table))
        if not mock_schema:
            return self.log_and_throw_exception(self._exception, "schema", f"({catalog}, {schema}, {table})")
        return mock_schema
