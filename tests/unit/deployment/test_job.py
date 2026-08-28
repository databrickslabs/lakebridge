import dataclasses
from unittest.mock import create_autospec

from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue
from databricks.sdk.service.jobs import Job

from databricks.labs.lakebridge.config import LakebridgeConfiguration, ReconcileJobConfig
from databricks.labs.lakebridge.deployment.job import JobDeployment


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


def test_deploy_new_job_defaults_to_serverless(oracle_recon_config):
    workspace_client = create_autospec(WorkspaceClient)
    job = Job(job_id=1234)
    workspace_client.jobs.create.return_value = job
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.from_class(LakebridgeConfiguration)
    name = "Recon Job"
    wheel_path = "/Workspace/user/.lakebridge/wheels/databricks_labs_lakebridge-x.y.z-py3-none-any.whl"
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_recon_job(name, oracle_recon_config, wheel_path)
    workspace_client.jobs.create.assert_called_once()
    assert install_state.jobs[name] == str(job.job_id)
    job_settings = workspace_client.jobs.create.call_args.kwargs
    assert "job_clusters" not in job_settings
    environments = job_settings["environments"]
    assert len(environments) == 1
    assert environments[0].environment_key == JobDeployment.SERVERLESS_ENVIRONMENT_KEY
    assert environments[0].spec.dependencies == [wheel_path]
    task = job_settings["tasks"][0]
    assert task.environment_key == JobDeployment.SERVERLESS_ENVIRONMENT_KEY
    assert task.libraries is None
    assert task.existing_cluster_id is None
    # No classic cluster APIs should be touched on the serverless path
    workspace_client.clusters.select_spark_version.assert_not_called()
    workspace_client.clusters.select_node_type.assert_not_called()


def test_deploy_new_job_with_existing_cluster(oracle_recon_config):
    workspace_client = create_autospec(WorkspaceClient)
    job = Job(job_id=1234)
    workspace_client.jobs.create.return_value = job
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.from_class(LakebridgeConfiguration)
    name = "Recon Job"
    wheel_path = "/Workspace/user/.lakebridge/wheels/databricks_labs_lakebridge-x.y.z-py3-none-any.whl"
    cluster_id = "0714-000000-abcdefgh"
    recon_config = dataclasses.replace(
        oracle_recon_config, job_overrides=ReconcileJobConfig(existing_cluster_id=cluster_id, tags={})
    )
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_recon_job(name, recon_config, wheel_path)
    workspace_client.jobs.create.assert_called_once()
    job_settings = workspace_client.jobs.create.call_args.kwargs
    assert "environments" not in job_settings
    task = job_settings["tasks"][0]
    assert task.existing_cluster_id == cluster_id
    assert task.environment_key is None
    assert [library.whl for library in task.libraries] == [wheel_path]


def test_deploy_new_job_with_blank_existing_cluster_falls_back_to_serverless(oracle_recon_config):
    workspace_client = create_autospec(WorkspaceClient)
    job = Job(job_id=1234)
    workspace_client.jobs.create.return_value = job
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    product_info = ProductInfo.from_class(LakebridgeConfiguration)
    name = "Recon Job"
    wheel_path = "/Workspace/user/.lakebridge/wheels/databricks_labs_lakebridge-x.y.z-py3-none-any.whl"
    # A blank existing_cluster_id must not leak an empty cluster reference into the spec: the Jobs API
    # rejects a task that carries both environment_key and existing_cluster_id="".
    recon_config = dataclasses.replace(
        oracle_recon_config, job_overrides=ReconcileJobConfig(existing_cluster_id="", tags={})
    )
    job_deployer = JobDeployment(workspace_client, installation, install_state, product_info)
    job_deployer.deploy_recon_job(name, recon_config, wheel_path)
    workspace_client.jobs.create.assert_called_once()
    job_settings = workspace_client.jobs.create.call_args.kwargs
    environments = job_settings["environments"]
    assert len(environments) == 1
    task = job_settings["tasks"][0]
    assert task.environment_key == JobDeployment.SERVERLESS_ENVIRONMENT_KEY
    assert task.existing_cluster_id is None
    assert task.libraries is None
    # The serialized spec must not carry an empty existing_cluster_id key alongside the environment.
    assert task.as_dict().get("existing_cluster_id") is None


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
