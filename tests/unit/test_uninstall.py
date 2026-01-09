from unittest.mock import create_autospec

import pytest
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import iam

from databricks.labs.lakebridge import uninstall
from databricks.labs.lakebridge.config import LakebridgeConfiguration
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.deployment.installation import WorkspaceInstallation
from databricks.labs.blueprint.tui import MockPrompts


@pytest.fixture
def ws():
    w = create_autospec(WorkspaceClient)
    w.current_user.me.side_effect = lambda: iam.User(
        user_name="me@example.com", groups=[iam.ComplexValue(display="admins")]
    )
    return w


def test_uninstaller_run(ws):
    prompts = MockPrompts(
        {
            r"Do you want to uninstall Lakebridge .*": "yes",
        }
    )
    ws_installation = create_autospec(WorkspaceInstallation)
    ctx = ApplicationContext(ws)
    ctx.replace(
        workspace_installation=ws_installation,
        remorph_config=LakebridgeConfiguration(transpile=None, reconcile=None),
        prompts=prompts,
    )
    uninstall.run(ctx)
    ws_installation.uninstall.assert_called_once()


def test_negative_uninstall_confirmation(ws):
    prompts = MockPrompts(
        {
            r"Do you want to uninstall Lakebridge .*": "no",
        }
    )
    ws_installation = create_autospec(WorkspaceInstallation)
    ctx = ApplicationContext(ws)
    ctx.replace(
        workspace_installation=ws_installation,
        remorph_config=LakebridgeConfiguration(transpile=None, reconcile=None),
        prompts=prompts,
    )
    uninstall.run(ctx)
    ws_installation.uninstall.assert_not_called()
