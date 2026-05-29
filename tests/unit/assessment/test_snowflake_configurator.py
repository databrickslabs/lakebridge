"""Unit tests for ConfigureSnowflakeAssessment."""

import yaml

from databricks.labs.blueprint.tui import MockPrompts

from databricks.labs.lakebridge.assessments.configure_assessment import (
    ConfigureSnowflakeAssessment,
)

# Prompts.choice sorts options alphabetically; "env" is index 0, "local" is index 1.
_VAULT_INDEX = {vault: idx for idx, vault in enumerate(sorted(["local", "env"]))}


def _prompts(vault_type: str, pat_prompt: str, pat_answer: str) -> MockPrompts:
    return MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": str(_VAULT_INDEX[vault_type]),
            r"Enter Snowflake account URL.*": "myorg-myaccount.snowflakecomputing.com",
            r"Enter username": "TEST_USER",
            r"Enter warehouse name": "COMPUTE_WH",
            r"Enter database name": "SNOWFLAKE",
            r"Enter schema name": "ACCOUNT_USAGE",
            r"Enter role": "ACCOUNTADMIN",
            pat_prompt: pat_answer,
            r"Do you want to test the connection to snowflake\?": "no",
        }
    )


def _run(prompts: MockPrompts, tmp_path):
    cred_file = tmp_path / ".credentials.yml"
    ConfigureSnowflakeAssessment(
        product_name="lakebridge",
        source_name="snowflake",
        prompts=prompts,
        credential_file=cred_file,
    ).run()
    with open(cred_file, encoding="utf-8") as file_handle:
        return yaml.safe_load(file_handle)


def test_local_vault_stores_pat_verbatim(tmp_path):
    prompts = _prompts("local", r"Enter Programmatic Access Token \(PAT\)", "fake-pat-token")
    creds = _run(prompts, tmp_path)
    assert creds == {
        "secret_vault_type": "local",
        "snowflake": {
            "connection": {
                "account": "myorg-myaccount.snowflakecomputing.com",
                "user": "TEST_USER",
                "warehouse": "COMPUTE_WH",
                "database": "SNOWFLAKE",
                "schema": "ACCOUNT_USAGE",
                "role": "ACCOUNTADMIN",
                "pat": "fake-pat-token",
            },
        },
    }


def test_env_vault_stores_env_var_name(tmp_path):
    # In env mode the stored value is the name of an environment variable that
    # EnvGetter resolves at runtime, so the prompt asks for a name, not the token.
    prompts = _prompts("env", r"Enter the environment variable name holding the PAT", "SNOWFLAKE_PAT")
    creds = _run(prompts, tmp_path)
    assert creds["secret_vault_type"] == "env"
    assert creds["snowflake"]["connection"]["pat"] == "SNOWFLAKE_PAT"
