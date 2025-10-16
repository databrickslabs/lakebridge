from databricks.labs.lakebridge.connections.database_manager import DatabaseManager


def get_sqlpool_reader(
    input_cred: dict,
    db_name: str,
    *,
    endpoint_key: str = 'dedicated_sql_endpoint',
    auth_type: str = 'sql_authentication',
) -> DatabaseManager:
    config = {
        "driver": input_cred['driver'],
        "server": input_cred[endpoint_key],
        "database": db_name,
        "user": input_cred['sql_user'],
        "password": input_cred['sql_password'],
        "port": input_cred.get('port', 1433),
        "auth_type": auth_type,
    }
    # synapse and mssql use the same connector
    source = "mssql"

    return DatabaseManager(source, config)
