from databricks.labs.blueprint.logger import install_logger
from databricks.labs.blueprint.entrypoint import get_logger
from databricks.sdk.core import with_user_agent_extra

install_logger()
with_user_agent_extra("cmd", "install")

if __name__ == "__main__":
    logger = get_logger(__file__)
    logger.setLevel("INFO")

    logger.info("Successfully Setup Lakebridge Components Locally")
    logger.info("For more information, please visit https://databrickslabs.github.io/lakebridge/")
