import sys

from databricks.sdk.core import with_user_agent_extra

from databricks.labs.lakebridge.cli import lakebridge
from databricks.labs.lakebridge.install import installer as _installer
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository


def main() -> None:
    with_user_agent_extra("cmd", "install")

    logger = lakebridge.get_logger()

    installer = _installer(
        ws=lakebridge.create_workspace_client(),
        transpiler_repository=TranspilerRepository.user_home(),
        is_interactive=sys.stdin.isatty(),
    )
    if not installer.upgrade_installed_transpilers():
        logger.debug("No existing Lakebridge transpilers detected; assuming fresh installation.")

    logger.info("Successfully Setup Lakebridge Components Locally")
    logger.info("For more information, please visit https://databrickslabs.github.io/lakebridge/")


if __name__ == "__main__":
    main()
