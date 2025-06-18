import json
from pathlib import Path
from unittest.mock import create_autospec
import logging
import pytest
from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.blueprint.installer import InstallState
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue, NotFound
from databricks.sdk.service.dashboards import Dashboard
from databricks.sdk.service.dashboards import LifecycleState

from databricks.labs.lakebridge.config import ReconcileMetadataConfig, ReconcileConfig, DatabaseConfig
from databricks.labs.lakebridge.deployment.dashboard import DashboardDeployment


def _get_dashboard_query(dashboard: Dashboard | None):
    if dashboard is None:
        return "Failed to get dashboard query"
    serialized_dashboard = json.loads(str(dashboard.serialized_dashboard))
    return serialized_dashboard['datasets'][0]['query']


def test_deploy_dashboard():
    ws = create_autospec(WorkspaceClient)
    expected_query = """SELECT
  main.recon_id,
  main.source_type,
  main.report_type,
  main.source_table.`catalog` AS source_catalog,
  main.source_table.`schema` AS source_schema,
  main.source_table.table_name AS source_table_name\nFROM remorph.reconcile.main AS main""".strip()

    dashboard_folder = Path(__file__).parent / Path("../../resources/dashboards")
    dashboard = Dashboard(
        dashboard_id="9c1fbf4ad3449be67d6cb64c8acc730b",
        display_name="Remorph-Reconciliation",
    )
    ws.lakeview.create.return_value = dashboard
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    dashboard_publisher = DashboardDeployment(ws, installation, install_state)
    reconcile_config = ReconcileConfig(
        data_source="oracle",
        report_type="all",
        secret_scope="remorph_oracle69",
        database_config=DatabaseConfig(
            source_schema="tpch_sf100069",
            target_catalog="tpch69",
            target_schema="1000gb69",
        ),
        metadata_config=ReconcileMetadataConfig(),
    )
    dashboard_publisher.deploy(dashboard_folder, reconcile_config)
    _, dash = ws.lakeview.create.call_args
    query = _get_dashboard_query(dash.get("dashboard"))
    assert query == expected_query
    assert install_state.dashboards["queries"] == dashboard.dashboard_id


@pytest.mark.parametrize("exception", [InvalidParameterValue, NotFound])
def test_recovery_invalid_dashboard(caplog, exception):
    dashboard_folder = Path(__file__).parent / Path("../../resources/dashboards")

    ws = create_autospec(WorkspaceClient)
    dashboard_id = "9c1fbf4ad3449be67d6cb64c8acc730b"
    dashboard = Dashboard(
        dashboard_id=dashboard_id,
        display_name="Remorph-Reconciliation",
    )
    ws.lakeview.create.return_value = dashboard
    ws.lakeview.get.side_effect = exception
    # name = "Remorph-Reconciliation"
    installation = MockInstallation(
        {
            "state.json": {
                "resources": {"dashboards": {"queries": "8c1fbf4ad3449be67d6cb64c8acc730b"}},
                "version": 1,
            },
        }
    )
    install_state = InstallState.from_installation(installation)
    dashboard_publisher = DashboardDeployment(ws, installation, install_state)
    reconcile_config = ReconcileConfig(
        data_source="oracle",
        report_type="all",
        secret_scope="remorph_oracle66",
        database_config=DatabaseConfig(
            source_schema="tpch_sf100066",
            target_catalog="tpch66",
            target_schema="1000gb66",
        ),
        metadata_config=ReconcileMetadataConfig(),
    )
    with caplog.at_level(logging.DEBUG, logger="databricks.labs.lakebridge.deployment.dashboard"):
        dashboard_publisher.deploy(dashboard_folder, reconcile_config)
    assert "Recovering invalid dashboard" in caplog.text
    assert "Deleted dangling dashboard" in caplog.text
    ws.workspace.delete.assert_called()
    ws.lakeview.create.assert_called()
    ws.lakeview.update.assert_not_called()


def test_recovery_trashed_dashboard(caplog):
    dashboard_folder = Path(__file__).parent / Path("../../resources/dashboards")

    ws = create_autospec(WorkspaceClient)
    dashboard_id = "9c1fbf4ad3449be67d6cb64c8acc730b"
    dashboard = Dashboard(
        dashboard_id=dashboard_id,
        display_name="Remorph-Reconciliation",
    )
    ws.lakeview.create.return_value = dashboard
    ws.lakeview.get.return_value = Dashboard(lifecycle_state=LifecycleState.TRASHED)
    installation = MockInstallation(
        {
            "state.json": {
                "resources": {"dashboards": {"queries": "8c1fbf4ad3449be67d6cb64c8acc730b"}},
                "version": 1,
            },
        }
    )
    install_state = InstallState.from_installation(installation)
    dashboard_publisher = DashboardDeployment(ws, installation, install_state)
    reconcile_config = ReconcileConfig(
        data_source="oracle",
        report_type="all",
        secret_scope="remorph_oracle77",
        database_config=DatabaseConfig(
            source_schema="tpch_sf100077",
            target_catalog="tpch77",
            target_schema="1000gb77",
        ),
        metadata_config=ReconcileMetadataConfig(),
    )
    with caplog.at_level(logging.DEBUG, logger="databricks.labs.lakebridge.deployment.dashboard"):
        dashboard_publisher.deploy(dashboard_folder, reconcile_config)
    assert "Recreating trashed dashboard" in caplog.text
    ws.lakeview.create.assert_called()
    ws.lakeview.update.assert_not_called()
