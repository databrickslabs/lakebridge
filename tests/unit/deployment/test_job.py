from unittest.mock import create_autospec

import pytest
from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue
from databricks.sdk.service.jobs import Job

from databricks.labs.lakebridge.config import (
    LakebridgeConfiguration,
    ReconcileConfig,
    ReconcileMetadataConfig,
    SourceConnectionConfig,
    TargetConnectionConfig,
    ProfilerDashboardConfig,
    ProfilerDashboardMetadataConfig,
)
from databricks.labs.lakebridge.deployment.job import JobDeployment


@pytest.fixture
def oracle_recon_config() -> ReconcileConfig:
    return ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="oracle",
            catalog="ORCL",
            schema="tpch_sf10009",
            uc_connection_name="remorph_oracle9",
        ),
        target=TargetConnectionConfig(
            catalog="tpch9",
            schema="1000gb9",
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
        report_type="all",
        source=SourceConnectionConfig(
            dialect="snowflake",
            catalog="snowflake_sample_data9",
            schema="tpch_sf10009",
            uc_connection_name="remorph_snowflake9",
        ),
        target=TargetConnectionConfig(
            catalog="tpch9",
            schema="1000gb9",
        ),
        metadata_config=ReconcileMetadataConfig(
            catalog="remorph9",
            schema="reconcile9",
            volume="reconcile_volume9",
        ),
    )


def test_deploy_existing_job(snowflake_recon_config):
    workspace_client = create_autospec(WorkspaceClient)
    workspace_client.config.is_gcp = True
    job_id = 1234
    job = Job(job_id=job_id)
    name = "Recon Job"
    installation = MockInstallation({"state.json": {"resources": {"jobs": {name: str(job_id)}}, "version": 1}})
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.for_testing(LakebridgeConfiguration)
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_recon_job(name, snowflake_recon_config, "lakebridge-x.y.z-py3-none-any.whl")
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
    product_info = ProductInfo.for_testing(LakebridgeConfiguration)
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_recon_job(name, snowflake_recon_config, "lakebridge-x.y.z-py3-none-any.whl")
    workspace_client.jobs.create.assert_called_once()
    assert install_state.jobs[name] == str(job.job_id)


def test_deploy_new_job(oracle_recon_config):
    workspace_client = create_autospec(WorkspaceClient)
    job = Job(job_id=1234)
    workspace_client.jobs.create.return_value = job
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.from_class(LakebridgeConfiguration)
    name = "Recon Job"
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_recon_job(name, oracle_recon_config, "lakebridge-x.y.z-py3-none-any.whl")
    workspace_client.jobs.create.assert_called_once()
    assert install_state.jobs[name] == str(job.job_id)


def test_parse_package_name() -> None:
    workspace_client = create_autospec(WorkspaceClient)
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.from_class(LakebridgeConfiguration)
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)

    assert job_deployer.parse_package_name("lakebridge-1.2.3-py3-none-any.whl") == "lakebridge"
    assert job_deployer.parse_package_name("remorph-1.2.3-py3-none-any.whl") == "databricks_labs_lakebridge"
    assert (
        job_deployer.parse_package_name("databricks_labs_lakebridge-1.2.3-py3-none-any.whl")
        == "databricks_labs_lakebridge"
    )
    assert (
        job_deployer.parse_package_name(
            "/Workspace/Users/username@@domain.com/.lakebridge/wheels/databricks_labs_lakebridge-0.10.7-py3-none-any.whl"
        )
        == "databricks_labs_lakebridge"
    )


def test_deploy_new_profiler_ingestion_job():
    workspace_client = create_autospec(WorkspaceClient)
    job = Job(job_id=5678)
    workspace_client.jobs.create.return_value = job
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.from_class(LakebridgeConfiguration)
    name = "Profiler Ingestion Job"
    config = ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path="/tmp/data/synapse_assessment/profiler_extract.db",
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge_profiler", schema="profiler_runs", volume="synapse-extract"
        ),
        job_overrides=None,
    )
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_profiler_ingestion_job(
        name,
        config,
        lakebridge_wheel_path="lakebridge-x.y.z-py3-none-any.whl",
    )
    workspace_client.jobs.create.assert_called_once()
    assert install_state.jobs[name] == str(job.job_id)


def test_deploy_existing_profiler_ingestion_job():
    workspace_client = create_autospec(WorkspaceClient)
    job_id = 5678
    job = Job(job_id=job_id)
    name = "Profiler Ingestion Job"
    # Create an existing state
    installation = MockInstallation({"state.json": {"resources": {"jobs": {name: str(job_id)}}, "version": 1}})
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.for_testing(LakebridgeConfiguration)
    config = ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path="/tmp/data/synapse_assessment/profiler_extract.db",
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge_profiler", schema="profiler_runs", volume="synapse-extract"
        ),
        job_overrides=None,
    )
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_profiler_ingestion_job(
        name,
        config,
        lakebridge_wheel_path="lakebridge-x.y.z-py3-none-any.whl",
    )
    workspace_client.jobs.reset.assert_called_once()
    assert install_state.jobs[name] == str(job.job_id)


def test_deploy_missing_profiler_ingestion_job():
    workspace_client = create_autospec(WorkspaceClient)
    job = Job(job_id=5678)
    workspace_client.jobs.create.return_value = job
    # Simulate a `Job not found` response
    workspace_client.jobs.reset.side_effect = InvalidParameterValue("Job not found")
    name = "Profiler Ingestion Job"
    installation = MockInstallation({"state.json": {"resources": {"jobs": {name: "9012"}}, "version": 1}})
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.for_testing(LakebridgeConfiguration)
    config = ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path="/tmp/data/synapse_assessment/profiler_extract.db",
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge_profiler", schema="profiler_runs", volume="synapse-extract"
        ),
        job_overrides=None,
    )
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_profiler_ingestion_job(
        name,
        config,
        lakebridge_wheel_path="lakebridge-x.y.z-py3-none-any.whl",
    )
    workspace_client.jobs.create.assert_called_once()
    assert install_state.jobs[name] == str(job.job_id)


def test_deploy_profiler_ingestion_job_uses_serverless_by_default(monkeypatch):
    monkeypatch.delenv("LAKEBRIDGE_PROFILER_INGESTION_USE_SERVERLESS", raising=False)
    workspace_client = create_autospec(WorkspaceClient)
    job = Job(job_id=5678)
    workspace_client.jobs.create.return_value = job
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.from_class(LakebridgeConfiguration)
    config = ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path="/tmp/data/synapse_assessment/profiler_extract.db",
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge_profiler", schema="profiler_runs", volume="synapse-extract"
        ),
        job_overrides=None,
    )
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_profiler_ingestion_job(
        "Profiler Ingestion Job",
        config,
        lakebridge_wheel_path="lakebridge-x.y.z-py3-none-any.whl",
    )

    call_kwargs = workspace_client.jobs.create.call_args.kwargs
    assert "job_clusters" not in call_kwargs
    assert "environments" in call_kwargs
    assert len(call_kwargs["environments"]) == 1
    assert call_kwargs["environments"][0].environment_key == "Lakebridge_Profiler_Ingest_Environment"
    assert call_kwargs["tasks"][0].job_cluster_key is None
    assert call_kwargs["tasks"][0].environment_key == "Lakebridge_Profiler_Ingest_Environment"
    assert call_kwargs["tasks"][0].libraries is None


def test_deploy_profiler_ingestion_job_can_use_classic_cluster(monkeypatch):
    monkeypatch.setenv("LAKEBRIDGE_PROFILER_INGESTION_USE_SERVERLESS", "false")
    workspace_client = create_autospec(WorkspaceClient)
    workspace_client.clusters.select_spark_version.return_value = "15.4.x-scala2.12"
    workspace_client.clusters.select_node_type.return_value = "m5.xlarge"
    job = Job(job_id=5678)
    workspace_client.jobs.create.return_value = job
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.from_class(LakebridgeConfiguration)
    config = ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path="/tmp/data/synapse_assessment/profiler_extract.db",
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge_profiler", schema="profiler_runs", volume="synapse-extract"
        ),
        job_overrides=None,
    )
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_profiler_ingestion_job(
        "Profiler Ingestion Job",
        config,
        lakebridge_wheel_path="lakebridge-x.y.z-py3-none-any.whl",
    )

    call_kwargs = workspace_client.jobs.create.call_args.kwargs
    assert "environments" not in call_kwargs
    assert "job_clusters" in call_kwargs
    assert len(call_kwargs["job_clusters"]) == 1
    assert call_kwargs["job_clusters"][0].job_cluster_key == "Lakebridge_Profiler_Ingest_Cluster"
    assert call_kwargs["tasks"][0].job_cluster_key == "Lakebridge_Profiler_Ingest_Cluster"
    assert call_kwargs["tasks"][0].environment_key is None
