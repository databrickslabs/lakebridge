from databricks.sdk.core import with_user_agent_extra

from databricks.labs.lakebridge.cli import lakebridge
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.install import initialize_logging


def run(context: ApplicationContext):
    context.workspace_installation.uninstall(context.remorph_config)


if __name__ == "__main__":
    with_user_agent_extra("cmd", "uninstall")
    initialize_logging()

    run(ApplicationContext(ws=lakebridge.create_workspace_client()))
