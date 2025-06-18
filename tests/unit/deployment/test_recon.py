from unittest.mock import create_autospec

import pytest
from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue
from databricks.sdk.service import iam

from databricks.labs.lakebridge.config import RemorphConfigs, ReconcileConfig, DatabaseConfig, ReconcileMetadataConfig
from databricks.labs.lakebridge.deployment.dashboard import DashboardDeployment
from databricks.labs.lakebridge.deployment.job import JobDeployment
from databricks.labs.lakebridge.deployment.recon import ReconDeployment
from databricks.labs.lakebridge.deployment.table import TableDeployment


@pytest.fixture
def ws():
    w = create_autospec(WorkspaceClient)
    w.current_user.me.side_effect = lambda: iam.User(
        user_name="me@example.com", groups=[iam.ComplexValue(display="admins")]
    )
    return w


def test_install_missing_config(ws):
    table_deployer = create_autospec(TableDeployment)
    job_deployer = create_autospec(JobDeployment)
    dashboard_deployer = create_autospec(DashboardDeployment)
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.for_testing(RemorphConfigs)
    recon_deployer = ReconDeployment(
        ws,
        installation,
        install_state,
        product_info,
        table_deployer,
        job_deployer,
        dashboard_deployer,
    )
    remorph_config = None
    recon_deployer.install(remorph_config, ["remorph-x.y.z-py3-none-any.whl"])
    table_deployer.deploy_table_from_ddl_file.assert_not_called()
    job_deployer.deploy_recon_job.assert_not_called()
    dashboard_deployer.deploy.assert_not_called()


def test_install(ws):
    reconcile_config = ReconcileConfig(
        data_source="snowflake",
        report_type="all",
        secret_scope="remorph_snowflake4",
        database_config=DatabaseConfig(
            source_catalog="snowflake_sample_data4",
            source_schema="tpch_sf10004",
            target_catalog="tpch4",
            target_schema="1000gb4",
        ),
        metadata_config=ReconcileMetadataConfig(
            catalog="remorph4",
            schema="reconcile4",
            volume="reconcile_volume4",
        ),
    )
    installation = MockInstallation(
        {
            "state.json": {
                "resources": {
                    "jobs": {
                        "Reconciliation Deprecated Job 1": "1",
                        "Reconciliation Deprecated Job 2": "2",
                        "Some other Job": "3",
                    },
                    "dashboards": {
                        "Reconciliation Deprecated Dashboard 1": "d_id1",
                        "Reconciliation Deprecated Dashboard 2": "d_id2",
                        "Some other Dashboard": "d_id3",
                    },
                },
                "version": 1,
            },
        }
    )
    table_deployer = create_autospec(TableDeployment)
    job_deployer = create_autospec(JobDeployment)
    dashboard_deployer = create_autospec(DashboardDeployment)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.for_testing(RemorphConfigs)
    recon_deployer = ReconDeployment(
        ws,
        installation,
        install_state,
        product_info,
        table_deployer,
        job_deployer,
        dashboard_deployer,
    )

    def raise_invalid_parameter_err_for_dashboard(rid: str):
        if rid == "d_id2":
            raise InvalidParameterValue

    def raise_invalid_parameter_err_for_job(jid: str):
        if jid == 2:
            raise InvalidParameterValue

    ws.lakeview.trash.side_effect = raise_invalid_parameter_err_for_dashboard
    ws.jobs.delete.side_effect = raise_invalid_parameter_err_for_job
    recon_deployer.install(reconcile_config, ["lakebridge-x.y.z-py3-none-any.whl"])
    table_deployer.deploy_table_from_ddl_file.assert_called()
    job_deployer.deploy_recon_job.assert_called()
    dashboard_deployer.deploy.assert_called()

    assert "Reconciliation Deprecated Job 1" not in install_state.jobs
    assert "Reconciliation Deprecated Job 2" not in install_state.jobs
    assert "Some other Job" in install_state.jobs


def test_uninstall_missing_config(ws):
    table_deployer = create_autospec(TableDeployment)
    job_deployer = create_autospec(JobDeployment)
    dashboard_deployer = create_autospec(DashboardDeployment)
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.for_testing(RemorphConfigs)
    recon_deployer = ReconDeployment(
        ws,
        installation,
        install_state,
        product_info,
        table_deployer,
        job_deployer,
        dashboard_deployer,
    )
    remorph_config = None
    recon_deployer.uninstall(remorph_config)
    ws.lakeview.trash.assert_not_called()
    ws.jobs.delete.assert_not_called()


def test_uninstall(ws):
    recon_config = ReconcileConfig(
        data_source="snowflake",
        report_type="all",
        secret_scope="remorph_snowflake5",
        database_config=DatabaseConfig(
            source_catalog="snowflake_sample_data5",
            source_schema="tpch_sf10005",
            target_catalog="tpch5",
            target_schema="1000gb5",
        ),
        metadata_config=ReconcileMetadataConfig(
            catalog="remorph5",
            schema="reconcile5",
            volume="reconcile_volume5",
        ),
    )
    installation = MockInstallation(
        {
            "state.json": {
                "resources": {
                    "jobs": {
                        "Reconciliation Runner": "15",
                        "Reconciliation Another Job": "25",
                        "Some other Job": "35",
                    },
                    "dashboards": {
                        "Reconciliation Metrics": "d_id15",
                        "Reconciliation Another Dashboard": "d_id25",
                        "Some other Dashboard": "d_id35",
                    },
                },
                "version": 1,
            },
        }
    )
    table_deployer = create_autospec(TableDeployment)
    job_deployer = create_autospec(JobDeployment)
    dashboard_deployer = create_autospec(DashboardDeployment)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.for_testing(RemorphConfigs)
    recon_deployer = ReconDeployment(
        ws,
        installation,
        install_state,
        product_info,
        table_deployer,
        job_deployer,
        dashboard_deployer,
    )

    def raise_invalid_parameter_err_for_dashboard(rid: str):
        if rid == "d_id25":
            raise InvalidParameterValue

    def raise_invalid_parameter_err_for_job(jid: str):
        if jid == 25:
            raise InvalidParameterValue

    ws.lakeview.trash.side_effect = raise_invalid_parameter_err_for_dashboard
    ws.jobs.delete.side_effect = raise_invalid_parameter_err_for_job

    recon_deployer.uninstall(recon_config)
    ws.lakeview.trash.assert_called()
    ws.jobs.delete.assert_called()

    assert "Reconciliation Runner" not in install_state.jobs
    assert "Some other Job" in install_state.jobs
    assert len(install_state.dashboards.keys()) == 0
