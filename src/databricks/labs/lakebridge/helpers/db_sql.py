import logging
import os

from databricks.labs.lsql.backends import (
    DatabricksConnectBackend,
    RuntimeBackend,
    SqlBackend,
    StatementExecutionBackend,
)
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)


def get_sql_backend(ws: WorkspaceClient, warehouse_id: str | None = None) -> SqlBackend:
    warehouse_id = warehouse_id or ws.config.warehouse_id
    if warehouse_id:
        logger.info(f"Using SQL backend with warehouse_id: {warehouse_id}")
        return StatementExecutionBackend(ws, warehouse_id)
    if "DATABRICKS_RUNTIME_VERSION" in os.environ:
        logger.info("Using SQL backend with Databricks Runtime.")
        return RuntimeBackend()
    logger.info("Using SQL backend with Databricks Connect.")
    return DatabricksConnectBackend(ws)
