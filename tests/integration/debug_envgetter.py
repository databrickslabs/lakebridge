import os
import json
import logging
from urllib.parse import urlparse

from databricks.labs.lakebridge.connections.env_getter import EnvGetter


class TestEnvGetter(EnvGetter):
    def __init__(self, is_debug: bool = True):
        super().__init__()
        self.is_debug = is_debug

    def _get_debug_env(self) -> dict:
        try:
            debug_env_file = f"{os.path.expanduser('~')}/.databricks/debug-env.json"
            with open(debug_env_file, 'r', encoding='utf-8') as file:
                contents = file.read()
            logging.debug(f"Found debug env file: {debug_env_file}")
            raw = json.loads(contents)
            return raw.get("ucws", {})
        except FileNotFoundError:
            return dict(os.environ)

    def get(self, key: str) -> str:
        if self.is_debug:
            self.env = self._get_debug_env()
        return super().get(key)


def parse_snowflake_jdbc_url(jdbc_url: str) -> dict:
    """
    Parse a Snowflake JDBC URL and return its components as a dictionary.

    Args:
        jdbc_url (str): The Snowflake JDBC URL to parse.

    Returns:
        dict: A dictionary containing the components of the JDBC URL.
    """
    if not jdbc_url.startswith("jdbc:snowflake://"):
        raise ValueError("Invalid Snowflake JDBC URL")

    url_parts = urlparse(jdbc_url.removeprefix("jdbc:"))
    server = url_parts.hostname
    query_params = dict(param.split("=", 1) for param in url_parts.query.split("&") if "=" in param)

    result = {'url': server, **query_params}

    return result
