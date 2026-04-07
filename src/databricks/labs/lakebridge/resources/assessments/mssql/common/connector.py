import logging

from databricks.labs.lakebridge.connections.database_manager import DatabaseManager
from databricks.labs.lakebridge.resources.assessments.mssql.common.queries import (
    AzureSQLDetect,
    AzureSQLQueries,
    MSSQLQueries,
)

logger = logging.getLogger(__name__)


def get_query_class(connection: DatabaseManager):
    """Return AzureSQLQueries if connected to Azure SQL DB (EngineEdition 5), else MSSQLQueries."""
    try:
        result = connection.fetch(AzureSQLDetect.is_azure_sql_db())
        if result.rows:
            engine_edition = result.rows[0][0]
            logger.info(f"Detected SQL Server EngineEdition: {engine_edition}")
            if engine_edition == 5:
                logger.info("Using Azure SQL Database compatible queries")
                return AzureSQLQueries
    except Exception as e:
        logger.warning(f"Could not detect engine edition, assuming on-prem: {e}")
    return MSSQLQueries


def get_sqlserver_reader(
    input_cred: dict,
    db_name: str,
    *,
    server_name: str,
    auth_type: str = 'sql_authentication',
) -> DatabaseManager:
    config = {
        "driver": input_cred['driver'],
        "server": server_name,
        "database": db_name,
        "user": input_cred['user'],
        "password": input_cred['password'],
        "port": input_cred.get('port', 1433),
        "auth_type": auth_type,
    }
    source = "mssql"

    return DatabaseManager(source, config)
