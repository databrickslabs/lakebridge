from unittest.mock import create_autospec
from typing import Any, cast
import pytest

from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.labs.lakebridge.config import LakebridgeConfiguration
from databricks.labs.lakebridge.deployment.job import JobDeployment
from databricks.labs.lakebridge.deployment.switch import SwitchDeployment
from databricks.sdk import WorkspaceClient, JobsExt
from databricks.sdk.errors import NotFound, InvalidParameterValue
from databricks.sdk.service.jobs import CreateResponse
from databricks.sdk.service.iam import User


@pytest.fixture()
def mock_workspace_client() -> WorkspaceClient:
    ws: Any = create_autospec(WorkspaceClient, instance=True)
    ws.current_user.me.return_value = User(user_name="test_user")
    ws.config.host = "https://test.databricks.com"
    ws.jobs = cast(Any, create_autospec(JobsExt, instance=True))
    return ws


@pytest.fixture()
def installation() -> MockInstallation:
    return MockInstallation(is_global=False)


@pytest.fixture()
def install_state(installation: MockInstallation) -> InstallState:
    return InstallState.from_installation(installation)


@pytest.fixture()
def product_info() -> ProductInfo:
    return ProductInfo.for_testing(LakebridgeConfiguration)


@pytest.fixture()
def job_deployer() -> JobDeployment:
    return create_autospec(JobDeployment, instance=True)


@pytest.fixture()
def switch_deployment(
    mock_workspace_client: Any,
    installation: MockInstallation,
    install_state: InstallState,
    product_info: ProductInfo,
    job_deployer: JobDeployment,
) -> SwitchDeployment:
    return SwitchDeployment(mock_workspace_client, installation, install_state)


def test_install_creates_job_successfully(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    """Test successful installation creates job and saves state."""
    mock_workspace_client.jobs.create.return_value = CreateResponse(job_id=123)

    switch_deployment.install()

    assert install_state.jobs["Switch"] == "123"
    mock_workspace_client.jobs.create.assert_called_once()


def test_install_updates_existing_job(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    """Test installation updates existing job if found."""
    install_state.jobs["Switch"] = "456"
    mock_workspace_client.jobs.get.return_value = create_autospec(CreateResponse, instance=True)

    switch_deployment.install()

    assert install_state.jobs["Switch"] == "456"
    mock_workspace_client.jobs.reset.assert_called_once()
    mock_workspace_client.jobs.create.assert_not_called()


def test_install_creates_new_job_when_existing_not_found(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    """Test installation creates new job when existing job is not found."""
    install_state.jobs["Switch"] = "789"
    mock_workspace_client.jobs.get.side_effect = NotFound("Job not found")
    mock_workspace_client.jobs.create.return_value = CreateResponse(job_id=999)

    switch_deployment.install()

    assert install_state.jobs["Switch"] == "999"
    mock_workspace_client.jobs.create.assert_called_once()


def test_install_handles_job_creation_error(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    """Test installation handles job creation errors gracefully."""
    mock_workspace_client.jobs.create.side_effect = RuntimeError("Job creation failed")

    with pytest.raises(SystemExit):
        switch_deployment.install()


def test_install_handles_invalid_parameter_error(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    """Test installation handles invalid parameter errors gracefully."""
    mock_workspace_client.jobs.create.side_effect = InvalidParameterValue("Invalid parameter")

    with pytest.raises(SystemExit):
        switch_deployment.install()


def test_install_fallback_on_update_failure(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    install_state.jobs["Switch"] = "555"
    mock_workspace_client.jobs.get.return_value = create_autospec(CreateResponse, instance=True)
    mock_workspace_client.jobs.reset.side_effect = InvalidParameterValue("Update failed")
    new_job = CreateResponse(job_id=666)
    mock_workspace_client.jobs.create.return_value = new_job

    switch_deployment.install()

    assert install_state.jobs["Switch"] == "666"
    mock_workspace_client.jobs.reset.assert_called_once()
    mock_workspace_client.jobs.create.assert_called_once()


def test_install_with_invalid_existing_job_id(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    install_state.jobs["Switch"] = "not_a_number"
    mock_workspace_client.jobs.get.side_effect = ValueError("Invalid job ID")
    new_job = CreateResponse(job_id=777)
    mock_workspace_client.jobs.create.return_value = new_job

    switch_deployment.install()

    assert install_state.jobs["Switch"] == "777"
    mock_workspace_client.jobs.create.assert_called_once()


def test_install_preserves_other_jobs_in_state(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    install_state.jobs["OtherJob"] = "999"
    new_job = CreateResponse(job_id=123)
    mock_workspace_client.jobs.create.return_value = new_job

    switch_deployment.install()

    assert install_state.jobs["Switch"] == "123"
    assert install_state.jobs["OtherJob"] == "999"


def test_install_configures_job_with_correct_parameters(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any
) -> None:
    """Test installation configures job with correct parameters."""
    new_job = CreateResponse(job_id=123)
    mock_workspace_client.jobs.create.return_value = new_job

    switch_deployment.install()

    # Verify job creation was called with settings
    mock_workspace_client.jobs.create.assert_called_once()
    call_kwargs = mock_workspace_client.jobs.create.call_args.kwargs

    # Verify job name
    assert call_kwargs["name"] == "Lakebridge_Switch"

    # Verify tags
    assert "created_by" in call_kwargs["tags"]
    assert call_kwargs["tags"]["created_by"] == "test_user"
    assert "switch_version" in call_kwargs["tags"]

    # Verify tasks
    assert len(call_kwargs["tasks"]) == 1
    assert call_kwargs["tasks"][0].task_key == "run_transpilation"
    assert call_kwargs["tasks"][0].disable_auto_optimization is True

    # Verify parameters
    param_names = {param.name for param in call_kwargs["parameters"]}
    assert param_names == {"catalog", "output_dir", "foundation_model", "schema", "source_tech", "input_dir"}

    # Verify max concurrent runs
    assert call_kwargs["max_concurrent_runs"] == 100


def test_install_configures_job_with_correct_notebook_path(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, installation: MockInstallation
) -> None:
    """Test installation configures job with correct notebook path."""
    new_job = CreateResponse(job_id=123)
    mock_workspace_client.jobs.create.return_value = new_job

    switch_deployment.install()

    call_kwargs = mock_workspace_client.jobs.create.call_args.kwargs
    notebook_path = call_kwargs["tasks"][0].notebook_task.notebook_path

    # Verify notebook path includes switch directory and notebook name
    assert "switch" in notebook_path
    assert "notebooks" in notebook_path
    assert "00_main" in notebook_path


def test_uninstall_removes_job_successfully(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    install_state.jobs["Switch"] = "123"

    switch_deployment.uninstall()

    assert "Switch" not in install_state.jobs
    mock_workspace_client.jobs.delete.assert_called_once_with(123)


def test_uninstall_handles_job_not_found(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    install_state.jobs["Switch"] = "456"
    mock_workspace_client.jobs.delete.side_effect = NotFound("Job not found")

    switch_deployment.uninstall()

    assert "Switch" not in install_state.jobs


def test_uninstall_handles_invalid_parameter(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    install_state.jobs["Switch"] = "789"
    mock_workspace_client.jobs.delete.side_effect = InvalidParameterValue("Invalid job ID")

    switch_deployment.uninstall()

    assert "Switch" not in install_state.jobs


def test_uninstall_no_job_in_state(switch_deployment: SwitchDeployment, mock_workspace_client: Any) -> None:
    switch_deployment.uninstall()

    mock_workspace_client.jobs.delete.assert_not_called()


def test_uninstall_with_invalid_job_id_format(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    install_state.jobs["Switch"] = "not_a_number"

    # Should raise ValueError when trying to convert to int
    with pytest.raises(ValueError):
        switch_deployment.uninstall()


def test_uninstall_preserves_other_jobs_in_state(
    switch_deployment: SwitchDeployment, mock_workspace_client: Any, install_state: InstallState
) -> None:
    install_state.jobs["Switch"] = "123"
    install_state.jobs["OtherJob"] = "999"

    switch_deployment.uninstall()

    assert "Switch" not in install_state.jobs
    assert install_state.jobs["OtherJob"] == "999"


# Parameterized tests


@pytest.mark.parametrize(
    "exception",
    [
        NotFound("Job not found"),
        InvalidParameterValue("Invalid parameter"),
    ],
)
def test_uninstall_handles_exceptions(
    switch_deployment: SwitchDeployment,
    mock_workspace_client: Any,
    install_state: InstallState,
    exception,
) -> None:
    install_state.jobs["Switch"] = "123"
    mock_workspace_client.jobs.delete.side_effect = exception

    switch_deployment.uninstall()

    assert "Switch" not in install_state.jobs


@pytest.mark.parametrize(
    "exception,expected_job_id",
    [
        (InvalidParameterValue("Update failed"), 888),
        (ValueError("Invalid job ID"), 777),
    ],
)
def test_install_creates_new_job_on_update_failure(
    switch_deployment: SwitchDeployment,
    mock_workspace_client: Any,
    install_state: InstallState,
    exception,
    expected_job_id,
) -> None:
    install_state.jobs["Switch"] = "555"
    mock_workspace_client.jobs.get.return_value = create_autospec(CreateResponse, instance=True)
    mock_workspace_client.jobs.reset.side_effect = exception
    new_job = CreateResponse(job_id=expected_job_id)
    mock_workspace_client.jobs.create.return_value = new_job

    switch_deployment.install()

    assert install_state.jobs["Switch"] == str(expected_job_id)
    mock_workspace_client.jobs.reset.assert_called_once()
    mock_workspace_client.jobs.create.assert_called_once()
