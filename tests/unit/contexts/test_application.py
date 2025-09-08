from unittest.mock import create_autospec

import pytest
from databricks.labs.blueprint.installation import MockInstallation
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import iam

from databricks.labs.lakebridge.contexts.application import ApplicationContext


@pytest.fixture
def ws():
    w = create_autospec(WorkspaceClient)
    w.current_user.me.side_effect = lambda: iam.User(
        user_name="me@example.com", groups=[iam.ComplexValue(display="admins")]
    )
    w.config.return_value = {"warehouse_id", "1234"}
    return w


def test_workspace_context_attributes_not_none(ws):
    ctx = ApplicationContext(ws)
    assert hasattr(ctx, "workspace_client")
    assert ctx.workspace_client is not None
    assert hasattr(ctx, "current_user")
    assert ctx.current_user.user_name == "me@example.com"
    assert hasattr(ctx, "product_info")
    assert ctx.product_info is not None
    assert hasattr(ctx, "connect_config")
    assert ctx.connect_config is not None
    assert hasattr(ctx, "catalog_operations")
    assert ctx.catalog_operations is not None
    assert hasattr(ctx, "installation")
    assert ctx.installation is not None
    assert hasattr(ctx, "sql_backend")
    assert ctx.sql_backend is not None
    assert hasattr(ctx, "prompts")
    assert ctx.prompts is not None

    ctx.replace(
        installation=MockInstallation(
            {
                "config.yml": {
                    "transpiler_config_path": "sqlglot",
                    "source_dialect": "snowflake",
                    "catalog_name": "transpiler_test",
                    "input_sql": "sf_queries",
                    "output_folder": "out_dir",
                    "skip_validation": False,
                    "schema_name": "convertor_test",
                    "sdk_config": {
                        "warehouse_id": "abc",
                    },
                    "version": 3,
                },
                "reconcile.yml": {
                    "data_source": "snowflake",
                    "database_config": {
                        "source_catalog": "snowflake_sample_data",
                        "source_schema": "tpch_sf1000",
                        "target_catalog": "tpch",
                        "target_schema": "1000gb",
                    },
                    "report_type": "all",
                    "secret_scope": "remorph_snowflake",
                    "tables": {
                        "filter_type": "exclude",
                        "tables_list": ["ORDERS", "PART"],
                    },
                    "metadata_config": {
                        "catalog": "remorph",
                        "schema": "reconcile",
                        "volume": "reconcile_volume",
                    },
                    "version": 1,
                },
                "state.json": {
                    "resources": {
                        "jobs": {"Remorph_Reconciliation_Job": "12345"},
                        "dashboards": {"Remorph-Reconciliation": "abcdef"},
                    },
                    "version": 1,
                },
            }
        )
    )
    assert hasattr(ctx, "transpile_config")
    assert ctx.transpile_config is not None
    assert hasattr(ctx, "recon_config")
    assert ctx.recon_config is not None
    assert hasattr(ctx, "remorph_config")
    assert ctx.remorph_config is not None
    assert ctx.remorph_config.transpile is not None
    assert ctx.remorph_config.reconcile is not None
    assert hasattr(ctx, "install_state")
    assert ctx.install_state is not None

    assert hasattr(ctx, "resource_configurator")
    assert ctx.resource_configurator is not None
    assert hasattr(ctx, "table_deployment")
    assert ctx.table_deployment is not None
    assert hasattr(ctx, "job_deployment")
    assert ctx.job_deployment is not None
    assert hasattr(ctx, "dashboard_deployment")
    assert ctx.dashboard_deployment is not None
    assert hasattr(ctx, "recon_deployment")
    assert ctx.recon_deployment is not None
    assert hasattr(ctx, "workspace_installation")
    assert ctx.workspace_installation is not None


def test_workspace_context_missing_configs(ws):
    ctx = ApplicationContext(ws)
    ctx.replace(installation=MockInstallation({}))
    assert hasattr(ctx, "transpile_config")
    assert ctx.transpile_config is None
    assert hasattr(ctx, "recon_config")
    assert ctx.recon_config is None
    assert hasattr(ctx, "remorph_config")
    assert ctx.remorph_config is not None
    assert ctx.remorph_config.transpile is None
    assert ctx.remorph_config.reconcile is None
