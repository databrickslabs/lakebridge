from databricks.sdk.core import with_user_agent_extra, with_product
from databricks.labs.blueprint.logger import install_logger
from databricks.labs.lakebridge.__about__ import __version__

install_logger()

# Add lakebridge/<version> for projects depending on lakebridge as a library
with_user_agent_extra("lakebridge", __version__)

# Add lakebridge/<version> for re-packaging of lakebridge, where product name is omitted
with_product("lakebridge", __version__)
