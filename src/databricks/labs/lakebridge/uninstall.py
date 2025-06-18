import logging

from databricks.labs.blueprint.entrypoint import is_in_debug
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.__about__ import __version__
from databricks.labs.lakebridge.contexts.application import ApplicationContext

logger = logging.getLogger("databricks.labs.lakebridge.install")


def run(context: ApplicationContext):
    context.workspace_installation.uninstall(context.remorph_config)


if __name__ == "__main__":
    logger.setLevel("INFO")
    if is_in_debug():
        logging.getLogger("databricks").setLevel(logging.DEBUG)

    run(
        ApplicationContext(
            WorkspaceClient(
                product="lakebridge",
                product_version=__version__,
            )
        )
    )
