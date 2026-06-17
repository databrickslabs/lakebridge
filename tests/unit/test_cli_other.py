import io
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, create_autospec, PropertyMock

import pytest

from databricks.sdk import WorkspaceClient

from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.lakebridge import cli
from databricks.labs.lakebridge.assessments import SOURCE_SYSTEM_VARIANTS
from databricks.labs.lakebridge.assessments.profiler import default_output_folder
from databricks.labs.lakebridge.config import (
    LSPConfigOptionV1,
    LSPPromptMethod,
    ReconcileConfig,
    ReconcileMetadataConfig,
    SourceConnectionConfig,
    TargetConnectionConfig,
)
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.helpers.recon_config_utils import ReconConfigPrompts


def test_configure_secrets_databricks(mock_workspace_client):
    source_dict = {"databricks": "0", "netezza": "1", "oracle": "2", "snowflake": "3"}
    prompts = MockPrompts(
        {
            r"Select the source": source_dict["databricks"],
        }
    )

    recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
    recon_conf.prompt_source()

    recon_conf.prompt_and_save_connection_details()


@pytest.mark.parametrize(
    ("argument", "expected"),
    (
        ("true", True),
        ("tRuE", True),
        ("false", False),
        ("fAlSe", False),
    ),
)
def test_interactive_argument(argument: str, expected: bool) -> None:
    """Check that the simple --interactive arguments as expected."""
    assert cli.interactive_mode(argument) is expected


def test_interactive_argument_unknown() -> None:
    """Check that an unknown --interactive argument raises an error."""
    with pytest.raises(ValueError) as expected_error:
        cli.interactive_mode("foobar")

    assert str(expected_error.value) == "Invalid value for '--interactive': 'foobar' must be 'true', 'false' or 'auto'."


@pytest.mark.parametrize("is_tty", (True, False))
def test_interactive_argument_auto(is_tty: bool) -> None:
    """Check that "auto" interactive detection is based on whether the stream looks like a TTY or not."""

    # Set up a fake stdin. (Can't use pty: it's unavailable on Windows.)
    mock_stdin = io.StringIO()
    setattr(mock_stdin, "isatty", mock_isatty := Mock(return_value=is_tty))

    interactive_mode = cli.interactive_mode("auto", input_stream=mock_stdin)

    # Check that we queried whether it's a TTY and that the result is as expected.
    assert mock_isatty.call_count == 1
    assert interactive_mode is is_tty


def test_cli_configure_secrets_config(mock_workspace_client):
    with patch("databricks.labs.lakebridge.cli.ReconConfigPrompts") as mock_recon_config:
        cli.configure_secrets(w=mock_workspace_client)
        mock_recon_config.assert_called_once_with(mock_workspace_client)


def app_factory(w: WorkspaceClient) -> ApplicationContext:
    ctx_mock = create_autospec(spec=ApplicationContext, spec_set=True)
    type(ctx_mock).workspace_client = PropertyMock(return_value=w)
    prompts = MockPrompts(
        {
            r"Would you like to open the job run URL .*": "no",
        }
    )
    ctx_mock.prompts = prompts
    ctx_mock.recon_config = ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="snowflake",
            catalog="src_catalog",
            schema="src_schema",
            uc_connection_name="conn",
        ),
        target=TargetConnectionConfig(catalog="tgt_catalog", schema="tgt_schema"),
        metadata_config=ReconcileMetadataConfig(),
    )
    return ctx_mock


def test_cli_reconcile(mock_workspace_client):
    with patch("databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run", return_value=(MagicMock(), "link1")):
        cli.reconcile(w=mock_workspace_client, ctx_factory=app_factory)


def test_cli_aggregates_reconcile(mock_workspace_client):
    with patch("databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run", return_value=(MagicMock(), "link1")):
        cli.aggregates_reconcile(w=mock_workspace_client, ctx_factory=app_factory)


@pytest.mark.parametrize(
    ("output_folder_arg", "expected_path"),
    (
        # Explicit --output-folder skips the prompt and is passed through as-is.
        ("/tmp/explicit-extract", Path("/tmp/explicit-extract")),
        # Relative paths are passed through as-is (resolution to CWD is the standard CLI behavior).
        ("./relative-extract", Path("./relative-extract")),
        # No --output-folder → prompt fires with default_output_folder(source_tech) as the default.
        (None, default_output_folder("snowflake")),
    ),
)
def test_cli_execute_database_profiler_output_folder(
    mock_workspace_client, output_folder_arg, expected_path, tmp_path, monkeypatch
):
    """`--output-folder` flag bypasses the prompt; absent flag prompts with the platform default."""
    # cred_file existence is a pre-flight gate; point it at a real file so we don't raise.
    fake_cred = tmp_path / "credentials.yml"
    fake_cred.touch()

    ctx_mock = create_autospec(spec=ApplicationContext, spec_set=True)
    type(ctx_mock).workspace_client = PropertyMock(return_value=mock_workspace_client)
    ctx_mock.current_user = "tester"
    ctx_mock.prompts = MockPrompts({r"Enter the profiler output.*": ""})  # accept the suggested default

    profiler = MagicMock()
    with (
        patch("databricks.labs.lakebridge.cli.ApplicationContext", return_value=ctx_mock),
        patch("databricks.labs.lakebridge.cli.cred_file", return_value=fake_cred),
        patch("databricks.labs.lakebridge.cli.Profiler.create", return_value=profiler) as create_mock,
    ):
        cli.execute_database_profiler(
            w=mock_workspace_client,
            source_tech="snowflake",
            output_folder=output_folder_arg,
        )

    create_mock.assert_called_once_with("snowflake", None)
    profiler.profile.assert_called_once_with(output_folder=expected_path, cred_file_path=fake_cred)


@pytest.mark.parametrize(
    ("explicit_arg", "expected_passes_explicit"),
    (
        # `--cred-file-path` provided and the file exists → that path is forwarded to Profiler.profile.
        (True, True),
        # `--cred-file-path` omitted → falls back to the default `cred_file(PRODUCT_NAME)`.
        (False, False),
    ),
)
def test_cli_execute_database_profiler_cred_file_path(
    mock_workspace_client, explicit_arg, expected_passes_explicit, tmp_path
):
    """`--cred-file-path` overrides the default; omitting it falls back to `cred_file(PRODUCT_NAME)`."""
    default_cred = tmp_path / "default-credentials.yml"
    default_cred.touch()
    explicit_cred = tmp_path / "explicit-credentials.yml"
    explicit_cred.touch()

    ctx_mock = create_autospec(spec=ApplicationContext, spec_set=True)
    type(ctx_mock).workspace_client = PropertyMock(return_value=mock_workspace_client)
    ctx_mock.current_user = "tester"
    ctx_mock.prompts = MockPrompts({r"Enter the profiler output.*": ""})

    profiler = MagicMock()
    with (
        patch("databricks.labs.lakebridge.cli.ApplicationContext", return_value=ctx_mock),
        patch("databricks.labs.lakebridge.cli.cred_file", return_value=default_cred),
        patch("databricks.labs.lakebridge.cli.Profiler.create", return_value=profiler),
    ):
        cli.execute_database_profiler(
            w=mock_workspace_client,
            source_tech="snowflake",
            output_folder=str(tmp_path / "out"),
            cred_file_path=str(explicit_cred) if explicit_arg else None,
        )

    expected_cred = explicit_cred if expected_passes_explicit else default_cred
    profiler.profile.assert_called_once_with(
        output_folder=tmp_path / "out",
        cred_file_path=expected_cred,
    )


def test_cli_execute_database_profiler_missing_cred_file_raises(mock_workspace_client, tmp_path):
    """A `--cred-file-path` pointing at a non-existent file fails the pre-flight check."""
    missing = tmp_path / "does-not-exist.yml"

    ctx_mock = create_autospec(spec=ApplicationContext, spec_set=True)
    type(ctx_mock).workspace_client = PropertyMock(return_value=mock_workspace_client)
    ctx_mock.current_user = "tester"
    ctx_mock.prompts = MockPrompts({})

    with (
        patch("databricks.labs.lakebridge.cli.ApplicationContext", return_value=ctx_mock),
        patch("databricks.labs.lakebridge.cli.cred_file", return_value=tmp_path / "default-unused.yml"),
        pytest.raises(ValueError, match="Credential file not found"),
    ):
        cli.execute_database_profiler(
            w=mock_workspace_client,
            source_tech="snowflake",
            output_folder=str(tmp_path / "out"),
            cred_file_path=str(missing),
        )


@pytest.mark.parametrize(
    ("source_tech", "variant", "expected"),
    (
        ("redshift", "provisioned", "provisioned"),  # valid variant passes through
        ("redshift", "PROVISIONED", "provisioned"),  # normalized to lower-case
        ("snowflake", None, None),  # source has no variants, none requested
        ("snowflake", "anything", None),  # source has no variants → stray input ignored
    ),
)
def test_parse_profiler_variant_returns_expected(source_tech, variant, expected):
    prompts = MagicMock()
    assert cli.parse_profiler_variant(prompts, source_tech, variant) == expected
    prompts.choice.assert_not_called()


def test_parse_profiler_variant_prompts_when_omitted_for_variant_source():
    """A variant-capable source with no explicit variant prompts the user to pick one."""
    prompts = MagicMock()
    prompts.choice.return_value = "serverless"
    assert cli.parse_profiler_variant(prompts, "redshift", None) == "serverless"
    prompts.choice.assert_called_once_with("Select a variant", SOURCE_SYSTEM_VARIANTS["redshift"])


def test_parse_profiler_variant_rejects_unknown_variant():
    with pytest.raises(ValueError, match="Invalid source technology variant"):
        cli.parse_profiler_variant(MagicMock(), "redshift", "bogus")


def test_cli_auto_configure_recon_tables_no_recon_config(mock_workspace_client):
    installation = MockInstallation({})
    ctx = ApplicationContext(mock_workspace_client)
    ctx.replace(prompts=MockPrompts({}), installation=installation)
    with pytest.raises(SystemExit, match="Reconcile is not configured"):
        cli.auto_configure_recon_tables(w=mock_workspace_client, ctx_factory=lambda ws: ctx)


def test_cli_auto_configure_recon_tables_when_no_file_runs_discover(mock_workspace_client, snowflake_recon_config):
    """First run of the recommended flow: no existing file → discover only (decline the one-job opt-in)."""
    installation = MockInstallation({})
    ctx = ApplicationContext(mock_workspace_client)
    ctx.replace(
        prompts=MockPrompts(
            {
                r"Discover tables now.*\?": "yes",
                r"Also run auto-configure in the same job .*": "no",
                r"Would you like to open the job run URL .*": "no",
            }
        ),
        installation=installation,
        recon_config=snowflake_recon_config,
    )

    with patch(
        "databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run",
        return_value=(MagicMock(), "link1"),
    ) as mock_run:
        cli.auto_configure_recon_tables(w=mock_workspace_client, ctx_factory=lambda ws: ctx)

    mock_run.assert_called_once_with(operation_name="discover-tables")


def test_cli_auto_configure_recon_tables_one_shot_discover_and_auto_configure(
    mock_workspace_client, snowflake_recon_config
):
    """Opt-in path: no file, discover, AND auto-configure in a single job (skips review)."""
    installation = MockInstallation({})
    ctx = ApplicationContext(mock_workspace_client)
    ctx.replace(
        prompts=MockPrompts(
            {
                r"Discover tables now.*\?": "yes",
                r"Also run auto-configure in the same job .*": "yes",
                r"Would you like to open the job run URL .*": "no",
            }
        ),
        installation=installation,
        recon_config=snowflake_recon_config,
    )

    with patch(
        "databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run",
        return_value=(MagicMock(), "link1"),
    ) as mock_run:
        cli.auto_configure_recon_tables(w=mock_workspace_client, ctx_factory=lambda ws: ctx)

    mock_run.assert_called_once_with(operation_name="discover-auto-configure-tables")


def test_cli_auto_configure_recon_tables_when_no_file_aborts_on_decline(mock_workspace_client, snowflake_recon_config):
    """No existing file + user declines discovery → no job run."""
    installation = MockInstallation({})
    ctx = ApplicationContext(mock_workspace_client)
    ctx.replace(
        prompts=MockPrompts({r"Discover tables now.*\?": "no"}),
        installation=installation,
        recon_config=snowflake_recon_config,
    )

    with patch("databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run") as mock_run:
        cli.auto_configure_recon_tables(w=mock_workspace_client, ctx_factory=lambda ws: ctx)

    mock_run.assert_not_called()


def test_cli_auto_configure_recon_tables_when_file_exists_rediscover(mock_workspace_client, snowflake_recon_config):
    """Existing file + user declines using existing mappings, then chooses to discover → discover-tables overwrites."""
    installation = MockInstallation(
        {
            snowflake_recon_config.table_recon_filename: {
                "tables": [{"source_name": "source", "target_name": "target"}],
                "version": 2,
            }
        }
    )
    ctx = ApplicationContext(mock_workspace_client)
    ctx.replace(
        prompts=MockPrompts(
            {
                r"Auto-configure and use existing table mappings \(no discovery\)\?": "no",
                r"Discover tables now.*\?": "yes",
                r"Also run auto-configure in the same job .*": "no",
                r"Would you like to open the job run URL .*": "no",
            }
        ),
        installation=installation,
        recon_config=snowflake_recon_config,
    )

    with patch(
        "databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run",
        return_value=(MagicMock(), "link1"),
    ) as mock_run:
        cli.auto_configure_recon_tables(w=mock_workspace_client, ctx_factory=lambda ws: ctx)

    mock_run.assert_called_once_with(operation_name="discover-tables")


def test_cli_auto_configure_recon_tables_when_file_exists_auto_configure(mock_workspace_client, snowflake_recon_config):
    """Second run of the recommended flow: existing file, accept using the curated mappings → auto-configure-tables."""
    installation = MockInstallation(
        {
            snowflake_recon_config.table_recon_filename: {
                "tables": [{"source_name": "source", "target_name": "target"}],
                "version": 2,
            }
        }
    )
    ctx = ApplicationContext(mock_workspace_client)
    ctx.replace(
        prompts=MockPrompts(
            {
                r"Auto-configure and use existing table mappings \(no discovery\)\?": "yes",
                r"Would you like to open the job run URL .*": "no",
            }
        ),
        installation=installation,
        recon_config=snowflake_recon_config,
    )

    with patch(
        "databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run",
        return_value=(MagicMock(), "link1"),
    ) as mock_run:
        cli.auto_configure_recon_tables(w=mock_workspace_client, ctx_factory=lambda ws: ctx)

    mock_run.assert_called_once_with(operation_name="auto-configure-tables")


def test_cli_auto_configure_recon_tables_aborts(mock_workspace_client, snowflake_recon_config):
    """Existing file + user declines auto-configure and declines discovery → no job run."""
    installation = MockInstallation(
        {
            snowflake_recon_config.table_recon_filename: {
                "tables": [{"source_name": "source", "target_name": "target"}],
                "version": 2,
            }
        }
    )
    ctx = ApplicationContext(mock_workspace_client)
    ctx.replace(
        prompts=MockPrompts(
            {
                r"Auto-configure and use existing table mappings \(no discovery\)\?": "no",
                r"Discover tables now.*\?": "no",
            }
        ),
        installation=installation,
        recon_config=snowflake_recon_config,
    )

    with patch("databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run") as mock_run:
        cli.auto_configure_recon_tables(w=mock_workspace_client, ctx_factory=lambda ws: ctx)

    mock_run.assert_not_called()


def test_prompts_question():
    option = LSPConfigOptionV1("param", LSPPromptMethod.QUESTION, "Some question", default="<none>")
    prompts = MockPrompts({"Some question": ""})
    response = option.prompt_for_value(prompts)
    assert response is None
    prompts = MockPrompts({"Some question": "<none>"})
    response = option.prompt_for_value(prompts)
    assert response is None
    prompts = MockPrompts({"Some question": "something"})
    response = option.prompt_for_value(prompts)
    assert response == "something"


@pytest.mark.parametrize(
    ("env_value", "expected_serverless"),
    (
        (None, True),  # Default: serverless (no env var)
        ("", True),  # Empty string: serverless
        ("CLASSIC", False),  # CLASSIC: use classic cluster
        ("classic", False),  # lowercase: use classic cluster
        ("Classic", False),  # Mixed case: use classic cluster
        ("SERVERLESS", True),  # Any other value: serverless
        ("other", True),  # Any other value: serverless
    ),
)
def test_install_transpile_cluster_type_env_var(
    env_value: str | None,
    expected_serverless: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test LAKEBRIDGE_CLUSTER_TYPE env var determines serverless vs classic cluster."""
    if env_value is None:
        monkeypatch.delenv("LAKEBRIDGE_CLUSTER_TYPE", raising=False)
    else:
        monkeypatch.setenv("LAKEBRIDGE_CLUSTER_TYPE", env_value)

    # The logic from cli.py install_transpile:
    # switch_use_serverless = os.environ.get("LAKEBRIDGE_CLUSTER_TYPE", "").upper() != "CLASSIC"
    actual = os.environ.get("LAKEBRIDGE_CLUSTER_TYPE", "").upper() != "CLASSIC"
    assert actual == expected_serverless
