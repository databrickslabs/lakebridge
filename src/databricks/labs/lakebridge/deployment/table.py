import logging
from importlib.abc import Traversable

from databricks.labs.lsql.backends import SqlBackend

logger = logging.getLogger(__name__)


class TableDeployment:
    def __init__(self, sql_backend: SqlBackend):
        self._sql_backend = sql_backend

    def deploy_table_from_ddl_file(
        self,
        catalog: str,
        schema: str,
        table_name: str,
        ddl_query_filepath: Traversable,
    ):
        """
        Deploys a table to the given catalog and schema
        :param catalog: The table catalog
        :param schema: The table schema
        :param table_name: The table to deploy
        :param ddl_query_filepath: DDL file path
        """
        query = ddl_query_filepath.read_text()
        logger.info(f"Deploying table {table_name} in {catalog}.{schema}")
        logger.info(f"SQL Backend used for deploying table: {type(self._sql_backend).__name__}")
        self._sql_backend.execute(query, catalog=catalog, schema=schema)
