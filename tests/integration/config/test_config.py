from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge.config import TranspileConfig, ReconcileConfig
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.install import WorkspaceInstaller


class _WorkspaceInstaller(WorkspaceInstaller):

    def save_config(self, config: TranspileConfig | ReconcileConfig):
        self._save_config(config)


def test_stores_and_fetches_config(ws):
    prompts = MockPrompts(
        {
            r"Open .* in the browser?": "no",
        }
    )
    context = ApplicationContext(ws)
    installer = _WorkspaceInstaller(
        context.workspace_client,
        prompts,
        context.installation,
        context.install_state,
        context.product_info,
        context.resource_configurator,
        context.workspace_installation,
    )
    config = TranspileConfig(
        transpiler_config_path="some_path",
        source_dialect="some_dialect",
        input_source="some_source",
        output_folder="some_output",
        error_file_path="some_file",
        transpiler_options={"b": "c", "tech-target": "PYSPARK"},
        sdk_config={"c": "d"},
        skip_validation=True,
        catalog_name="some_catalog",
        schema_name="some_schema",
    )
    installer.save_config(config)
    retrieved = ApplicationContext(ws).transpile_config
    assert retrieved == config
