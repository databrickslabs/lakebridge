from databricks.labs.lakebridge.connections.database_manager import DatabaseManager


def get_sqlserver_reader(
    input_cred: dict,
    db_name: str,
    *,
    fully_qualified_domain_name: str,
    auth_type: str = 'sql_authentication',
) -> DatabaseManager:
    config = {
        "driver": input_cred['driver'],
        "server": fully_qualified_domain_name,
        "database": db_name,
        "user": input_cred['sql_user'],
        "password": input_cred['sql_password'],
        "port": input_cred.get('port', 1433),
        "auth_type": auth_type,
    }
    source = "mssql"

    return DatabaseManager(source, config)
