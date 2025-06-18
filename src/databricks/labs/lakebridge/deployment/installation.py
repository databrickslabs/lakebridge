import logging
from ast import literal_eval
from pathlib import Path

from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.tui import Prompts
from databricks.labs.blueprint.upgrades import Upgrades
from databricks.labs.blueprint.wheels import ProductInfo, Version
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.mixins.compute import SemVer
from databricks.sdk.errors.platform import InvalidParameterValue, ResourceDoesNotExist

from databricks.labs.lakebridge.config import RemorphConfigs
from databricks.labs.lakebridge.deployment.recon import ReconDeployment

logger = logging.getLogger("databricks.labs.lakebridge.install")


class WorkspaceInstallation:
    def __init__(
        self,
        ws: WorkspaceClient,
        prompts: Prompts,
        installation: Installation,
        recon_deployment: ReconDeployment,
        product_info: ProductInfo,
        upgrades: Upgrades,
    ):
        self._ws = ws
        self._prompts = prompts
        self._installation = installation
        self._recon_deployment = recon_deployment
        self._product_info = product_info
        self._upgrades = upgrades

    def _get_local_version_file_path(self):
        user_home = f"{Path(__file__).home()}"
        return Path(f"{user_home}/.databricks/labs/{self._product_info.product_name()}/state/version.json")

    def _get_local_version_file(self, file_path: Path):
        data = None
        with file_path.open("r") as f:
            data = literal_eval(f.read())
        assert data, "Unable to read local version file."
        local_installed_version = data["version"]
        try:
            SemVer.parse(local_installed_version)
        except ValueError:
            logger.warning(f"{local_installed_version} is not a valid version.")
            local_installed_version = "v0.3.0"
        local_installed_date = data["date"]
        logger.debug(f"Found local installation version: {local_installed_version} {local_installed_date}")
        return Version(
            version=local_installed_version,
            date=local_installed_date,
            wheel=f"databricks_labs_remorph-{local_installed_version}-py3-none-any.whl",
        )

    def _get_ws_version(self):
        try:
            return self._installation.load(Version)
        except ResourceDoesNotExist as err:
            logger.warning(f"Unable to get Workspace Version due to: {err}")
            return None

    def _apply_upgrades(self):
        """
        * If remote version doesn't exist and local version exists:
           Upload Version file to workspace to handle previous installations.
        * If remote version or local_version exists, then only apply upgrades.
        * No need to apply upgrades for fresh installation.
        """
        ws_version = self._get_ws_version()
        local_version_path = self._get_local_version_file_path()
        local_version = local_version_path.exists()
        if not ws_version and local_version:
            self._installation.save(self._get_local_version_file(local_version_path))

        if ws_version or local_version:
            try:
                self._upgrades.apply(self._ws)
                logger.debug("Upgrades applied successfully.")
            except (InvalidParameterValue, NotFound) as err:
                logger.warning(f"Unable to apply Upgrades due to: {err}")

    def _upload_wheel(self):
        wheels = self._product_info.wheels(self._ws)
        with wheels:
            wheel_paths = [wheels.upload_to_wsfs()]
            wheel_paths = [f"/Workspace{wheel}" for wheel in wheel_paths]
            return wheel_paths

    def install(self, config: RemorphConfigs):
        self._apply_upgrades()
        wheel_paths: list[str] = self._upload_wheel()
        if config.reconcile:
            logger.info("Installing Remorph reconcile Metadata components.")
            self._recon_deployment.install(config.reconcile, wheel_paths)

    def uninstall(self, config: RemorphConfigs):
        # This will remove all the Remorph modules
        if not self._prompts.confirm(
            "Do you want to uninstall Remorph from the workspace too, this would "
            "remove Remorph project folder, jobs, metadata and dashboards"
        ):
            return
        logger.info(f"Uninstalling Remorph from {self._ws.config.host}.")
        try:
            self._installation.files()
        except NotFound:
            logger.error(f"Check if {self._installation.install_folder()} is present. Aborting uninstallation.")
            return

        if config.transpile:
            logging.info(
                f"Won't remove transpile validation schema `{config.transpile.schema_name}` "
                f"from catalog `{config.transpile.catalog_name}`. Please remove it manually."
            )

        if config.reconcile:
            self._recon_deployment.uninstall(config.reconcile)

        self._installation.remove()
        logger.info("Uninstallation completed successfully.")
