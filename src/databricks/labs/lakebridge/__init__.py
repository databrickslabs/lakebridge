import logging

from databricks.sdk.core import with_user_agent_extra, with_product
from databricks.labs.blueprint.entrypoint import is_in_debug
from databricks.labs.blueprint.logger import install_logger
from databricks.labs.lakebridge.__about__ import __version__

# Ensure that anything that imports this (or lower) submodules triggers setup of the blueprint logging.
install_logger()


def initialize_logging() -> None:
    """Common logging initialisation for non-CLI entry-points."""
    # This is intended to be used by all the non-CLI entry-points, such as install/uninstall hooks and pipeline tasks.
    # It emulates the behaviour of the blueprint App() initialisation, except that we don't have handoff from the
    # Databricks CLI. As such the policy is:
    #   - The root (and logging system in general) is left alone.
    #   - If running in the IDE debugger, databricks.* will be set to DEBUG.
    #   - Otherwise, databricks.* will be set to INFO.
    databricks_log_level = logging.DEBUG if is_in_debug() else logging.INFO
    logging.getLogger("databricks").setLevel(databricks_log_level)


# Add lakebridge/<version> for projects depending on lakebridge as a library
with_user_agent_extra("lakebridge", __version__)

# Add lakebridge/<version> for re-packaging of lakebridge, where product name is omitted
with_product("lakebridge", __version__)
