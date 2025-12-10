from unittest.mock import create_autospec, Mock
import pytest
from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.tui import MockPrompts
from databricks.sdk import WorkspaceClient
from databricks.labs.lakebridge.reconcile.runner import ReconcileRunner
from databricks.labs.lakebridge.deployment.recon import RECON_JOB_NAME


def test_run_with_missing_job_id():
    ws = create_autospec(WorkspaceClient)
    installation = MockInstallation(
        {
            "reconcile.yml": {
                "data_source": "snowflake",
                "database_config": {
                    "source_catalog": "abc",
                    "source_schema": "def",
                    "target_catalog": "tgt",
                    "target_schema": "sch",
                },
                "report_type": "all",
                "secret_scope": "remorph",
                "tables": {
                    "filter_type": "all",
                    "tables_list": ["*"],
                },
                "metadata_config": {
                    "catalog": "remorph",
                    "schema": "reconcile",
                    "volume": "reconcile_volume",
                },
                "version": 1,
            },
            "recon_config_snowflake_abc_all.json": {
                "tables": [
                    {
                        "column_mapping": [
                            {"source_name": "p_id", "target_name": "product_id"},
                            {"source_name": "p_name", "target_name": "product_name"},
                        ],
                        "join_columns": ["p_id"],
                        "select_columns": ["p_id", "p_name"],
                        "source_name": "product",
                        "target_name": "product_delta",
                    }
                ],
            },
        }
    )
    install_state = InstallState.from_installation(installation)
    prompts = MockPrompts({})
    recon_runner = ReconcileRunner(ws, install_state, prompts)
    with pytest.raises(SystemExit):
        recon_runner.run()


def test_run_with_job_id_in_state(monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    ws = create_autospec(WorkspaceClient)
    prompts = MockPrompts(
        {
            r"Would you like to open the job run URL .*": "yes",
        }
    )
    installation = MockInstallation(
        {
            "state.json": {
                "resources": {"jobs": {RECON_JOB_NAME: "1234"}},
                "version": 1,
            },
            "reconcile.yml": {
                "data_source": "snowflake",
                "database_config": {
                    "source_catalog": "abc",
                    "source_schema": "def",
                    "target_catalog": "tgt",
                    "target_schema": "sch",
                },
                "report_type": "all",
                "secret_scope": "remorph",
                "tables": {
                    "filter_type": "all",
                    "tables_list": ["*"],
                },
                "metadata_config": {
                    "catalog": "remorph",
                    "schema": "reconcile",
                    "volume": "reconcile_volume",
                },
                "version": 1,
            },
            "recon_config_snowflake_abc_all.json": {
                "tables": [
                    {
                        "column_mapping": [
                            {"source_name": "p_id", "target_name": "product_id"},
                            {"source_name": "p_name", "target_name": "product_name"},
                        ],
                        "join_columns": ["p_id"],
                        "select_columns": ["p_id", "p_name"],
                        "source_name": "product",
                        "target_name": "product_delta",
                    }
                ],
            },
        }
    )
    install_state = InstallState.from_installation(installation)
    wait = Mock()
    wait.run_id = "rid"
    ws.jobs.run_now.return_value = wait

    recon_runner = ReconcileRunner(ws, install_state, prompts)
    recon_runner.run()
    ws.jobs.run_now.assert_called_once_with(1234, job_parameters={'operation_name': 'reconcile'})


def test_run_with_failed_execution():
    ws = create_autospec(WorkspaceClient)
    installation = MockInstallation(
        {
            "state.json": {
                "resources": {"jobs": {RECON_JOB_NAME: "1234"}},
                "version": 1,
            },
            "reconcile.yml": {
                "data_source": "snowflake",
                "database_config": {
                    "source_catalog": "abc",
                    "source_schema": "def",
                    "target_catalog": "tgt",
                    "target_schema": "sch",
                },
                "report_type": "all",
                "secret_scope": "remorph",
                "tables": {
                    "filter_type": "all",
                    "tables_list": ["*"],
                },
                "metadata_config": {
                    "catalog": "remorph",
                    "schema": "reconcile",
                    "volume": "reconcile_volume",
                },
                "version": 1,
            },
            "recon_config_snowflake_abc_all.json": {
                "tables": [
                    {
                        "column_mapping": [
                            {"source_name": "p_id", "target_name": "product_id"},
                            {"source_name": "p_name", "target_name": "product_name"},
                        ],
                        "join_columns": ["p_id"],
                        "select_columns": ["p_id", "p_name"],
                        "source_name": "product",
                        "target_name": "product_delta",
                    }
                ],
            },
        }
    )
    install_state = InstallState.from_installation(installation)
    prompts = MockPrompts({})
    wait = Mock()
    wait.run_id = None
    ws.jobs.run_now.return_value = wait

    recon_runner = ReconcileRunner(ws, install_state, prompts)
    with pytest.raises(SystemExit):
        recon_runner.run()
    ws.jobs.run_now.assert_called_once_with(1234, job_parameters={'operation_name': 'reconcile'})


def test_aggregates_reconcile_run_with_job_id_in_state(monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    ws = create_autospec(WorkspaceClient)
    prompts = MockPrompts(
        {
            r"Would you like to open the job run URL .*": "yes",
        }
    )
    state = {
        "resources": {"jobs": {RECON_JOB_NAME: "1234"}},
        "version": 1,
    }

    reconcile = {
        "data_source": "snowflake",
        "database_config": {
            "source_catalog": "abc",
            "source_schema": "def",
            "target_catalog": "tgt",
            "target_schema": "sch",
        },
        "report_type": "all",
        "secret_scope": "remorph",
        "metadata_config": {
            "catalog": "remorph",
            "schema": "reconcile",
            "volume": "reconcile_volume",
        },
        "version": 1,
    }

    sf_recon_config = {
        "tables": [
            {
                "aggregates": [
                    {"type": "MIN", "agg_columns": ["discount"], "group_by_columns": ["p_id"]},
                    {"type": "AVG", "agg_columns": ["discount"], "group_by_columns": ["p_id"]},
                    {"type": "MAX", "agg_columns": ["p_id"], "group_by_columns": ["creation_date"]},
                    {"type": "MAX", "agg_columns": ["p_name"]},
                    {"type": "SUM", "agg_columns": ["p_id"]},
                    {"type": "MAX", "agg_columns": ["creation_date"]},
                    {"type": "MAX", "agg_columns": ["p_id"], "group_by_columns": ["creation_date"]},
                ],
                "column_mapping": [
                    {"source_name": "p_id", "target_name": "product_id"},
                    {"source_name": "p_name", "target_name": "product_name"},
                ],
                "join_columns": ["p_id"],
                "select_columns": ["p_id", "p_name"],
                "source_name": "product",
                "target_name": "product_delta",
            }
        ],
    }

    installation = MockInstallation(
        {
            "state.json": state,
            "reconcile.yml": reconcile,
            "recon_config_snowflake_abc_all.json": sf_recon_config,
        }
    )
    install_state = InstallState.from_installation(installation)
    wait = Mock()
    wait.run_id = "rid"
    ws.jobs.run_now.return_value = wait

    recon_runner = ReconcileRunner(ws, install_state, prompts)
    recon_runner.run(operation_name="aggregates-reconcile")
    ws.jobs.run_now.assert_called_once_with(1234, job_parameters={'operation_name': 'aggregates-reconcile'})
