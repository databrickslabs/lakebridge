from databricks.sdk.core import with_user_agent_extra

from databricks.labs.lakebridge.cli import lakebridge
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge import initialize_logging


def run(context: ApplicationContext):
    if context.prompts.confirm(
        "Do you want to uninstall Lakebridge from the workspace too, this would "
        "remove Lakebridge project folder, jobs, metadata and dashboards"
    ):
        context.workspace_installation.uninstall(context.remorph_config)


if __name__ == "__main__":
    with_user_agent_extra("cmd", "uninstall")
    initialize_logging()

    run(ApplicationContext(ws=lakebridge.create_workspace_client()))
