import os
from collections.abc import Mapping

from databricks.sdk.useragent import alphanum_pattern, semver_pattern

# Entrypoint constants
DEFAULT_ENTRYPOINT = "cli"
VALID_ENTRYPOINTS = {DEFAULT_ENTRYPOINT, "desktop-app", "databricks-app"}


def make_alphanum_or_semver(value: str) -> str:
    if alphanum_pattern.match(value) or semver_pattern.match(value):
        return value
    # assume it's not a semver, replace illegal alphanum chars
    result = []
    for char in value:
        if not alphanum_pattern.match(char):
            char = '_'
        result.append(char)
    return "".join(result)


def get_entrypoint_from_env(environ: Mapping[str, str] | None = None) -> str:
    """Detect execution entrypoint from LAKEBRIDGE_ENTRYPOINT env var.

    Args:
        environ: Optional environment dict. If None, uses os.environ.

    Returns:
        One of "cli", "desktop-app", or "databricks-app". Defaults to "cli" if
        the environment variable is missing or contains an invalid value.
    """
    if environ is None:
        environ = os.environ
    entrypoint = environ.get("LAKEBRIDGE_ENTRYPOINT")
    if not entrypoint:
        return DEFAULT_ENTRYPOINT

    normalized = entrypoint.lower().strip()

    return normalized if normalized in VALID_ENTRYPOINTS else DEFAULT_ENTRYPOINT
