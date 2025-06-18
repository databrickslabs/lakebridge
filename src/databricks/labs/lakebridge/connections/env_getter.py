import os


class EnvGetter:
    """Standardised inorder to support testing Capabilities, check debug_envgetter.py"""

    def __init__(self):
        self.env = dict(os.environ)

    def get(self, key: str) -> str:
        if key in self.env:
            return self.env[key]
        raise KeyError(f"not in env: {key}")
