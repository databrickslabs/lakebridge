from unittest.mock import create_autospec

import pytest
from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue
from databricks.sdk.service.jobs import Job

from databricks.labs.lakebridge.config import RemorphConfigs, ReconcileConfig, DatabaseConfig, ReconcileMetadataConfig
from databricks.labs.lakebridge.deployment.job import JobDeployment


@pytest.fixture
def oracle_recon_config() -> ReconcileConfig:
    return ReconcileConfig(
        data_source="oracle",
        report_type="all",
        secret_scope="remorph_oracle9",
        database_config=DatabaseConfig(
            source_schema="tpch_sf10009",
            target_catalog="tpch9",
            target_schema="1000gb9",
        ),
        metadata_config=ReconcileMetadataConfig(
            catalog="remorph9",
            schema="reconcile9",
            volume="reconcile_volume9",
        ),
    )


@pytest.fixture
def snowflake_recon_config() -> ReconcileConfig:
    return ReconcileConfig(
        data_source="snowflake",
        report_type="all",
        secret_scope="remorph_snowflake9",
        database_config=DatabaseConfig(
            source_schema="tpch_sf10009",
            target_catalog="tpch9",
            target_schema="1000gb9",
            source_catalog="snowflake_sample_data9",
        ),
        metadata_config=ReconcileMetadataConfig(
            catalog="remorph9",
            schema="reconcile9",
            volume="reconcile_volume9",
        ),
    )


def test_deploy_new_job(oracle_recon_config):
    workspace_client = create_autospec(WorkspaceClient)
    job = Job(job_id=1234)
    workspace_client.jobs.create.return_value = job
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.from_class(RemorphConfigs)
    name = "Recon Job"
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_recon_job(name, oracle_recon_config, "remorph-x.y.z-py3-none-any.whl")
    workspace_client.jobs.create.assert_called_once()
    assert install_state.jobs[name] == str(job.job_id)


def test_deploy_existing_job(snowflake_recon_config):
    workspace_client = create_autospec(WorkspaceClient)
    workspace_client.config.is_gcp = True
    job_id = 1234
    job = Job(job_id=job_id)
    name = "Recon Job"
    installation = MockInstallation({"state.json": {"resources": {"jobs": {name: str(job_id)}}, "version": 1}})
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.for_testing(RemorphConfigs)
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_recon_job(name, snowflake_recon_config, "remorph-x.y.z-py3-none-any.whl")
    workspace_client.jobs.reset.assert_called_once()
    assert install_state.jobs[name] == str(job.job_id)


def test_deploy_missing_job(snowflake_recon_config):
    workspace_client = create_autospec(WorkspaceClient)
    job_id = 1234
    job = Job(job_id=job_id)
    workspace_client.jobs.create.return_value = job
    workspace_client.jobs.reset.side_effect = InvalidParameterValue("Job not found")
    name = "Recon Job"
    installation = MockInstallation({"state.json": {"resources": {"jobs": {name: "5678"}}, "version": 1}})
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.for_testing(RemorphConfigs)
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_recon_job(name, snowflake_recon_config, "remorph-x.y.z-py3-none-any.whl")
    workspace_client.jobs.create.assert_called_once()
    assert install_state.jobs[name] == str(job.job_id)
