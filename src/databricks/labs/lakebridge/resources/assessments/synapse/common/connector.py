from databricks.labs.lakebridge.connections.database_manager import DatabaseManager


def get_sqlpool_reader(
    input_cred: dict,
    db_name: str,
    *,
    endpoint_key: str = 'dedicated_sql_endpoint',
    auth_type: str = 'sql_authentication',
) -> DatabaseManager:
    """
    Create Synapse SQL pool reader.

    Args:
        input_cred: Synapse workspace credentials with keys:
            - dedicated_sql_endpoint or serverless_sql_endpoint: SQL endpoint
            - sql_user: SQL username
            - sql_password: SQL password
            - driver: ODBC driver name (e.g., "ODBC Driver 18 for SQL Server")
            - port (optional): Port number, defaults to 1433
        db_name: SQL pool database name
        endpoint_key: 'dedicated_sql_endpoint' or 'serverless_sql_endpoint'
        auth_type: Authentication type (sql_authentication, ad_passwd_authentication, etc.)

    Returns:
        DatabaseManager instance configured for Synapse SQL pool
    """
    config = {
        "endpoint_key": endpoint_key,
        "driver": input_cred['driver'],
        "server": input_cred[endpoint_key],
        "database": db_name,
        "sql_user": input_cred['sql_user'],
        "sql_password": input_cred['sql_password'],
        "user": input_cred['sql_user'],
        "password": input_cred['sql_password'],
        "port": input_cred.get('port', 1433),
        "auth_type": auth_type,
    }
    # Use synapse connector which inherits from mssql
    source = "synapse"

    return DatabaseManager(source, config)
