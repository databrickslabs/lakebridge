import logging
from datetime import timedelta
from pathlib import Path

from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.lsql.dashboards import DashboardMetadata, Dashboards
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue, NotFound, DeadlineExceeded, InternalError
from databricks.sdk.retries import retried
from databricks.sdk.service.dashboards import LifecycleState, Dashboard
from databricks.sdk.errors.platform import ResourceAlreadyExists
from databricks.labs.lakebridge.config import ReconcileMetadataConfig

logger = logging.getLogger(__name__)


class DashboardDeployment:

    def __init__(
        self,
        ws: WorkspaceClient,
        installation: Installation,
        install_state: InstallState,
    ):
        self._ws = ws
        self._installation = installation
        self._install_state = install_state

    def deploy(
        self,
        folder: Path,
        metadata_config: ReconcileMetadataConfig,
    ):
        """
        Create dashboards from Dashboard metadata files.
        The given folder is expected to contain subfolders each containing metadata for individual dashboards.

        :param folder: Path to the base folder.
        :param metadata_config: Meta configuration for reconciliation.
        """
        logger.info(f"Deploying dashboards from base folder {folder}")
        parent_path = f"{self._installation.install_folder()}/dashboards"
        try:
            self._ws.workspace.mkdirs(parent_path)
        except ResourceAlreadyExists:
            logger.info(f"Dashboard parent path already exists: {parent_path}")

        valid_dashboard_refs = set()
        for entry in folder.iterdir():
            if entry.is_dir() and entry.joinpath("dashboard.yml").exists():
                # YAML + lsql path
                valid_dashboard_refs.add(self._dashboard_reference(entry))
                dashboard = self._update_or_create_dashboard(entry, parent_path, metadata_config)
                logger.info(
                    f"Dashboard deployed with URL: {self._ws.config.host}/sql/dashboardsv3/{dashboard.dashboard_id}"
                )
                self._install_state.save()
            elif entry.is_file() and entry.name.endswith(".lvdash.json"):
                # Raw-JSON path: source-of-truth is the serialized Lakeview JSON,
                # bypassing lsql so manual UI edits (conditional formatting, etc.)
                # survive a round-trip through the deploy.
                valid_dashboard_refs.add(self._dashboard_reference(entry))
                dashboard_id = self._update_or_create_json_dashboard(entry, parent_path, metadata_config)
                logger.info(
                    f"Dashboard deployed with URL: {self._ws.config.host}/sql/dashboardsv3/{dashboard_id}"
                )
                self._install_state.save()

        self._remove_deprecated_dashboards(valid_dashboard_refs)

    def _dashboard_reference(self, entry: Path) -> str:
        # For ``foo.lvdash.json``, drop both suffixes so the reference key matches the
        # corresponding YAML-based ``foo/`` directory's install_state entry. That lets
        # the JSON cutover update the existing dashboard in place rather than create a
        # new one + trash the old.
        name = entry.name
        if name.endswith(".lvdash.json"):
            return name[: -len(".lvdash.json")].lower()
        return entry.stem.lower()

    def _update_or_create_json_dashboard(
        self,
        json_path: Path,
        ws_parent_path: str,
        config: ReconcileMetadataConfig,
    ) -> str:
        # Substitute the placeholder catalog/schema baked into the source JSON
        # with this workspace's configured values (mirrors lsql's replace_database).
        raw = json_path.read_text()
        raw = raw.replace("remorph.reconcile.", f"{config.catalog}.{config.schema}.")

        meta_path = json_path.with_name(json_path.name.replace(".lvdash.json", ".lvdash.meta.json"))
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            display_name_raw = meta.get("display_name", self._dashboard_reference(json_path))
        else:
            display_name_raw = self._dashboard_reference(json_path)
        display_name = self._name_with_prefix(display_name_raw)

        reference = self._dashboard_reference(json_path)
        dashboard_id = self._install_state.dashboards.get(reference)

        if dashboard_id is not None:
            try:
                dashboard_id = self._handle_existing_dashboard(dashboard_id, display_name)
            except (NotFound, InvalidParameterValue):
                logger.info(f"Recovering invalid dashboard: {display_name} ({dashboard_id})")
                try:
                    dashboard_path = f"{ws_parent_path}/{display_name}.lvdash.json"
                    self._ws.workspace.delete(dashboard_path)
                except NotFound:
                    pass
                dashboard_id = None

        if dashboard_id is None:
            created = self._ws.lakeview.create(
                dashboard=Dashboard(
                    display_name=display_name,
                    parent_path=ws_parent_path,
                    serialized_dashboard=raw,
                    warehouse_id=self._ws.config.warehouse_id,
                )
            )
            dashboard_id = created.dashboard_id
        else:
            self._ws.lakeview.update(
                dashboard_id,
                dashboard=Dashboard(
                    display_name=display_name,
                    serialized_dashboard=raw,
                    warehouse_id=self._ws.config.warehouse_id,
                ),
            )

        self._ws.lakeview.publish(
            dashboard_id,
            embed_credentials=True,
            warehouse_id=self._ws.config.warehouse_id,
        )
        assert dashboard_id is not None
        self._install_state.dashboards[reference] = dashboard_id
        return dashboard_id

    # InternalError and DeadlineExceeded are retried because of Lakeview internal issues
    # These issues have been reported to and are resolved by the Lakeview team
    # Keeping the retry for resilience
    @retried(on=[InternalError, DeadlineExceeded], timeout=timedelta(minutes=3))
    def _update_or_create_dashboard(
        self,
        folder: Path,
        ws_parent_path: str,
        config: ReconcileMetadataConfig,
    ) -> Dashboard:
        logger.info(f"Reading dashboard folder {folder}")
        metadata = DashboardMetadata.from_path(folder).replace_database(
            catalog=config.catalog,
            catalog_to_replace="remorph",
            database=config.schema,
            database_to_replace="reconcile",
        )

        metadata.display_name = self._name_with_prefix(metadata.display_name)
        reference = self._dashboard_reference(folder)
        dashboard_id = self._install_state.dashboards.get(reference)
        if dashboard_id is not None:
            try:
                dashboard_id = self._handle_existing_dashboard(dashboard_id, metadata.display_name)
            except (NotFound, InvalidParameterValue):
                logger.info(f"Recovering invalid dashboard: {metadata.display_name} ({dashboard_id})")
                try:
                    dashboard_path = f"{ws_parent_path}/{metadata.display_name}.lvdash.json"
                    self._ws.workspace.delete(dashboard_path)  # Cannot recreate dashboard if file still exists
                    logger.debug(
                        f"Deleted dangling dashboard {metadata.display_name} ({dashboard_id}): {dashboard_path}"
                    )
                except NotFound:
                    pass
                dashboard_id = None  # Recreate the dashboard if it's reference is corrupted (manually)

        dashboard = Dashboards(self._ws).create_dashboard(
            metadata,
            dashboard_id=dashboard_id,
            parent_path=ws_parent_path,
            warehouse_id=self._ws.config.warehouse_id,
            publish=True,
        )
        assert dashboard.dashboard_id is not None
        self._install_state.dashboards[reference] = dashboard.dashboard_id
        return dashboard

    def _name_with_prefix(self, name: str) -> str:
        prefix = self._installation.product()
        return f"{prefix.upper()}_{name}".replace(" ", "_")

    def _handle_existing_dashboard(self, dashboard_id: str, display_name: str) -> str | None:
        dashboard = self._ws.lakeview.get(dashboard_id)
        if dashboard.lifecycle_state is None:
            raise NotFound(f"Dashboard life cycle state: {display_name} ({dashboard_id})")
        if dashboard.lifecycle_state == LifecycleState.TRASHED:
            logger.info(f"Recreating trashed dashboard: {display_name} ({dashboard_id})")
            return None  # Recreate the dashboard if it is trashed (manually)
        return dashboard_id  # Update the existing dashboard

    def _remove_deprecated_dashboards(self, valid_dashboard_refs: set[str]):
        # Snapshot the items so we can `del` entries inside the loop.
        for ref, dashboard_id in list(self._install_state.dashboards.items()):
            if ref not in valid_dashboard_refs:
                try:
                    logger.info(f"Removing dashboard_id={dashboard_id}, as it is no longer needed.")
                    del self._install_state.dashboards[ref]
                    self._ws.lakeview.trash(dashboard_id)
                except (InvalidParameterValue, NotFound):
                    logger.warning(f"Dashboard `{dashboard_id}` doesn't exist anymore for some reason.")
                    continue
