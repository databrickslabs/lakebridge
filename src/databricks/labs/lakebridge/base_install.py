from databricks.labs.blueprint.logger import install_logger
from databricks.labs.blueprint.entrypoint import get_logger
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import with_user_agent_extra

from databricks.labs.lakebridge import __version__
from databricks.labs.lakebridge.install import installer as _installer
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository


def main() -> None:
    install_logger()
    with_user_agent_extra("cmd", "install")

    logger = get_logger(__file__)
    logger.setLevel("INFO")

    installer = _installer(
        WorkspaceClient(product="lakebridge", product_version=__version__),
        transpiler_repository=TranspilerRepository.user_home(),
    )
    if installer.has_installed_transpilers():
        logger.warning(
            "Detected existing Lakebridge transpilers; run 'databricks labs lakebridge install-transpile' to upgrade them."
        )
    else:
        logger.debug("No existing Lakebridge transpilers detected; assuming fresh installation.")

    logger.info("Successfully Setup Lakebridge Components Locally")
    logger.info("For more information, please visit https://databrickslabs.github.io/lakebridge/")


if __name__ == "__main__":
    main()
