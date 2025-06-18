import os
import json
import logging

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
