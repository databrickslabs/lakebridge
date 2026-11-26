import json
import logging
from pathlib import Path, PurePosixPath
from unittest.mock import call, create_autospec

import pytest
from databricks.labs.blueprint.tui import MockPrompts
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InternalError
from databricks.sdk.service.iam import User

from databricks.labs.lakebridge import cli
from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.transpiler.agentic.converter import (
    AgenticConversionError,
    AgenticConversionResult,
    AgenticConverter,
)

SUMMARY: dict[str, int | str | None] = {
    "total_files_processed": 3,
    "total_queries_processed": 2,
    "analysis_error_count": 0,
    "parsing_error_count": 0,
    "validation_error_count": 0,
    "generation_error_count": 1,
    "error_log_file": "errors.log",
}


@pytest.fixture(name="input_source")
def fixture_input_source(tmp_path: Path) -> Path:
    source = tmp_path / "input"
    source.mkdir()
    (source / "orders.sql").write_text("SELECT 1;")
    return source


@pytest.fixture(name="ctx")
def fixture_ctx() -> ApplicationContext:
    context = create_autospec(ApplicationContext).return_value
    context.transpile_config = None
    context.prompts = MockPrompts({})
    context.current_user = User(user_name="someone@example.com")
    return context


@pytest.fixture(name="converter")
def fixture_converter() -> AgenticConverter:
    agentic = create_autospec(AgenticConverter, instance=True)
    agentic.convert.return_value = AgenticConversionResult(dict(SUMMARY))
    return agentic


def _transpile(ctx: ApplicationContext, converter: AgenticConverter, **kwargs) -> None:
    cli.transpile(w=create_autospec(WorkspaceClient), ctx=ctx, agentic_converter=converter, **kwargs)


@pytest.mark.parametrize(
    "selection", [{"agentic": "true"}, {"transpiler": "agentic"}, {"agentic": "TRUE", "transpiler": "Agentic"}]
)
def test_converts_with_the_agentic_code_converter(ctx, converter, input_source, tmp_path, capsys, selection) -> None:
    _transpile(
        ctx,
        converter,
        source_dialect="snowflake",
        target_technology="pyspark",
        input_source=str(input_source),
        output_folder=str(tmp_path / "out"),
        error_file_path=str(tmp_path / "errors.log"),
        skills="/Workspace/skills/a, /Workspace/skills/b",
        **selection,
    )

    kwargs = converter.convert.call_args.kwargs
    assert kwargs["sources"].relative_paths == (PurePosixPath("orders.sql"),)
    assert kwargs["source_dialect"] == "snowflake"
    assert kwargs["target_technology"] == "PYSPARK"
    assert kwargs["user_name"] == "someone@example.com"
    assert kwargs["output_folder"] == tmp_path / "out"
    assert kwargs["error_path"] == tmp_path / "errors.log"
    assert kwargs["skill_paths"] == ["/Workspace/skills/a", "/Workspace/skills/b"]
    assert json.loads(capsys.readouterr().out) == [SUMMARY]
    ctx.add_user_agent_extra.assert_has_calls(
        [
            call("transpiler_source_tech", "snowflake"),
            call("transpiler_plugin_name", "agentic"),
            call("transpiler_target_tech", "PYSPARK"),
            call("transpiler_file_count", "1"),
        ]
    )


def test_uses_the_stored_configuration_and_prompts_for_what_is_missing(ctx, converter, input_source, tmp_path) -> None:
    ctx.transpile_config = TranspileConfig(
        input_source=str(input_source),
        output_folder=str(tmp_path / "out"),
        transpiler_config_path="/an/installed/transpiler/config.yml",
        transpiler_options={"target-tech": "SPARKSQL"},
    )
    ctx.prompts = MockPrompts({r"Select the source dialect": "2"})

    _transpile(ctx, converter, agentic="true")

    kwargs = converter.convert.call_args.kwargs
    assert kwargs["source_dialect"] == "oracle"
    assert kwargs["target_technology"] == "SPARKSQL"
    assert kwargs["output_folder"] == tmp_path / "out"


def test_prompts_for_the_target_technology(ctx, converter, input_source, tmp_path) -> None:
    ctx.prompts = MockPrompts({r"Select the target technology": "0"})

    _transpile(
        ctx,
        converter,
        agentic="true",
        source_dialect="mssql",
        input_source=str(input_source),
        output_folder=str(tmp_path),
    )

    assert converter.convert.call_args.kwargs["target_technology"] == "PYSPARK"


def test_ignores_settings_that_dont_apply_with_a_warning(ctx, converter, input_source, tmp_path, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        _transpile(
            ctx,
            converter,
            agentic="true",
            source_dialect="snowflake",
            target_technology="SPARKSQL",
            input_source=str(input_source),
            output_folder=str(tmp_path),
            transpiler_config_path="/does/not/exist.yml",
            catalog_name="my_catalog",
        )

    assert "Ignoring --transpiler-config-path" in caplog.text
    assert "Ignoring --catalog-name" in caplog.text
    converter.convert.assert_called_once()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"agentic": "yes"}, "Invalid value for '--agentic'"),
        ({"transpiler": "bladebridge"}, "Invalid value for '--transpiler': 'bladebridge'"),
        ({"agentic": "false", "transpiler": "agentic"}, "Conflicting values"),
        ({"agentic": "true", "source_dialect": "netezza"}, "'netezza' must be one of: bigquery, mssql, oracle"),
        ({"agentic": "true", "target_technology": "SCALA"}, "'--target-technology': 'SCALA' must be one of"),
        ({"agentic": "true", "skills": "relative/skill"}, "must be a workspace path"),
        ({"agentic": "true", "skills": ",".join(f"/s/{i}" for i in range(11))}, "at most 10 skill folders"),
        ({"skills": "/Workspace/skills/a"}, "'--skills' only applies to the agentic code converter"),
    ],
)
def test_rejects_invalid_settings_before_converting(ctx, converter, input_source, tmp_path, kwargs, message) -> None:
    settings = {"source_dialect": "snowflake", "target_technology": "SPARKSQL", **kwargs}
    with pytest.raises(ValueError, match=message):
        _transpile(ctx, converter, input_source=str(input_source), output_folder=str(tmp_path), **settings)

    converter.convert.assert_not_called()


def test_rejects_a_stored_target_technology_the_converter_doesnt_take(ctx, converter, input_source, tmp_path) -> None:
    ctx.transpile_config = TranspileConfig(transpiler_options={"target-tech": "Scala"})

    with pytest.raises(ValueError, match="Invalid configured target technology"):
        _transpile(
            ctx,
            converter,
            agentic="true",
            source_dialect="snowflake",
            input_source=str(input_source),
            output_folder=str(tmp_path),
        )


def test_exits_non_zero_when_the_conversion_fails(ctx, converter, input_source, tmp_path, caplog) -> None:
    converter.convert.side_effect = AgenticConversionError("The agentic code converter can't convert files here.")

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exited:
        _transpile(
            ctx,
            converter,
            agentic="true",
            source_dialect="snowflake",
            target_technology="SPARKSQL",
            input_source=str(input_source),
            output_folder=str(tmp_path),
        )

    assert exited.value.code == 1
    assert "can't convert files here" in caplog.text


def test_prints_the_summary_and_exits_non_zero_when_the_conversion_stops_early(
    ctx, converter, input_source, tmp_path, capsys
) -> None:
    converter.convert.return_value = AgenticConversionResult(dict(SUMMARY), "Conversion run 1 was cancelled.")

    with pytest.raises(SystemExit) as exited:
        _transpile(
            ctx,
            converter,
            agentic="true",
            source_dialect="snowflake",
            target_technology="SPARKSQL",
            input_source=str(input_source),
            output_folder=str(tmp_path),
        )

    assert exited.value.code == 1
    assert json.loads(capsys.readouterr().out) == [SUMMARY]


def test_exits_non_zero_on_an_api_error_the_converter_lets_through(ctx, converter, input_source, tmp_path) -> None:
    converter.convert.side_effect = InternalError("unexpected")

    with pytest.raises(SystemExit) as exited:
        _transpile(
            ctx,
            converter,
            agentic="true",
            source_dialect="snowflake",
            target_technology="SPARKSQL",
            input_source=str(input_source),
            output_folder=str(tmp_path),
        )

    assert exited.value.code == 1
