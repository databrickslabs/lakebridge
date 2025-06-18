from urllib.parse import urlparse
from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager
from .debug_envgetter import TestEnvGetter


def get_db_manager(product_name: str, source: str) -> DatabaseManager:
    env = TestEnvGetter(True)
    config = create_credential_manager(product_name, env).get_credentials(source)

    # since the kv has only URL so added explicit parse rules
    base_url, params = config['server'].replace("jdbc:", "", 1).split(";", 1)

    url_parts = urlparse(base_url)
    server = url_parts.hostname
    query_params = dict(param.split("=", 1) for param in params.split(";") if "=" in param)
    database = query_params.get("database", "")
    config['server'] = server
    config['database'] = database

    return DatabaseManager(source, config)
