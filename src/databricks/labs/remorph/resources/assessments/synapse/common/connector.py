from pathlib import Path
import yaml
from databricks.labs.remorph.connections.credential_manager import (
    CredentialManager,
    LocalSecretProvider,
    EnvSecretProvider,
    DatabricksSecretProvider,
)

from databricks.labs.remorph.connections.env_getter import EnvGetter
from databricks.labs.remorph.resources.assessments.synapse.common.functions import get_synapse_jdbc_settings
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, Result, URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text


def _load_credentials(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Credentials file not found at {path}") from e


def create_credential_manager(file_path: Path):
    env_getter = EnvGetter()

    secret_providers = {
        'local': LocalSecretProvider(),
        'env': EnvSecretProvider(env_getter),
        'databricks': DatabricksSecretProvider(),
    }

    loader = _load_credentials(file_path)
    return CredentialManager(loader, secret_providers)


def get_sqlpool_reader(config: dict, db_name: str, endpoint_key='dedicated_sql_endpoint'):
    """
    :param config:
    :param db_name:
    :return: returns a sqlachemy reader for the given dedicated SQL Pool database
    """

    query_params = {
        "driver": config['driver'],
        "loginTimeout": "30",
    }

    connection_string = URL.create(
        "mssql+pyodbc",
        username=config['sql_user'],
        password=config['sql_password'],
        host=config[endpoint_key],
        port=config.get('port', 1433),
        database=db_name,
        query=query_params,
    )
    engine = create_engine(connection_string)
    session = sessionmaker(bind=engine)
    connection = session()

    return connection
