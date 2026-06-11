"""Unit tests for the Redshift-aware short-circuit in ProfilerDashboardDeployment.

Redshift variants share no profiler dashboard template, so ``install`` must upload
the DuckDB extract to UC Volume (same delivery as other profilers) and then return
early without attempting to deploy a dashboard or ingestion job.
"""

from unittest.mock import create_autospec

import pytest

from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import (
    ProfilerDashboardConfig,
    ProfilerDashboardMetadataConfig,
)
from databricks.labs.lakebridge.deployment.dashboard import ProfilerDashboardManager
from databricks.labs.lakebridge.deployment.job import JobDeployment
from databricks.labs.lakebridge.deployment.profiler_dashboard import (
    ProfilerDashboardDeployment,
)


def _redshift_config(variant: str) -> ProfilerDashboardConfig:
    return ProfilerDashboardConfig(
        source_tech=f"redshift_{variant}",
        extract_file_path="/tmp/data/redshift_assessment/profiler_extract.db",
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge",
            schema="profiler",
            volume="ingestion_volume",
        ),
    )


@pytest.mark.parametrize("variant", ["serverless", "provisioned", "provisioned_multi_az"])
def test_install_uploads_duckdb_and_skips_dashboard_for_redshift(variant: str) -> None:
    ws = create_autospec(WorkspaceClient, instance=True)
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = create_autospec(ProductInfo, instance=True)
    job_deployer = create_autospec(JobDeployment, instance=True)
    dashboard_manager = create_autospec(ProfilerDashboardManager, instance=True)
    dashboard_manager.upload_duckdb_to_uc_volume.return_value = True

    deployment = ProfilerDashboardDeployment(
        ws, installation, install_state, product_info, job_deployer, dashboard_manager
    )

    deployment.install(_redshift_config(variant), wheel_path="/tmp/lakebridge.whl")

    dashboard_manager.upload_duckdb_to_uc_volume.assert_called_once()
    dashboard_manager.deploy.assert_not_called()
    job_deployer.deploy_profiler_ingestion_job.assert_not_called()


def test_install_aborts_when_duckdb_upload_fails_for_redshift() -> None:
    ws = create_autospec(WorkspaceClient, instance=True)
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = create_autospec(ProductInfo, instance=True)
    job_deployer = create_autospec(JobDeployment, instance=True)
    dashboard_manager = create_autospec(ProfilerDashboardManager, instance=True)
    dashboard_manager.upload_duckdb_to_uc_volume.return_value = False

    deployment = ProfilerDashboardDeployment(
        ws, installation, install_state, product_info, job_deployer, dashboard_manager
    )

    deployment.install(_redshift_config("provisioned"), wheel_path="/tmp/lakebridge.whl")

    dashboard_manager.upload_duckdb_to_uc_volume.assert_called_once()
    dashboard_manager.deploy.assert_not_called()
    job_deployer.deploy_profiler_ingestion_job.assert_not_called()


def test_install_deploys_full_pipeline_for_non_redshift_source() -> None:
    ws = create_autospec(WorkspaceClient, instance=True)
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = create_autospec(ProductInfo, instance=True)
    job_deployer = create_autospec(JobDeployment, instance=True)
    dashboard_manager = create_autospec(ProfilerDashboardManager, instance=True)
    dashboard_manager.upload_duckdb_to_uc_volume.return_value = True
    dashboard_manager.deploy.return_value = "dash_id_123"

    deployment = ProfilerDashboardDeployment(
        ws, installation, install_state, product_info, job_deployer, dashboard_manager
    )

    config = ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path="/tmp/data/synapse_assessment/profiler_extract.db",
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge", schema="profiler", volume="ingestion_volume"
        ),
    )

    deployment.install(config, wheel_path="/tmp/lakebridge.whl")

    dashboard_manager.upload_duckdb_to_uc_volume.assert_called_once()
    dashboard_manager.deploy.assert_called_once_with(config)
    job_deployer.deploy_profiler_ingestion_job.assert_called_once()
