import logging

from databricks.labs.blueprint.entrypoint import is_in_debug
from databricks.sdk.core import with_user_agent_extra

from databricks.labs.lakebridge.cli import lakebridge
from databricks.labs.lakebridge.contexts.application import ApplicationContext

logger = logging.getLogger("databricks.labs.lakebridge.install")


def run(context: ApplicationContext):
    context.workspace_installation.uninstall(context.remorph_config)


if __name__ == "__main__":
    with_user_agent_extra("cmd", "uninstall")

    logger.setLevel("INFO")
    if is_in_debug():
        logging.getLogger("databricks").setLevel(logging.DEBUG)

    run(ApplicationContext(ws=lakebridge.create_workspace_client()))
