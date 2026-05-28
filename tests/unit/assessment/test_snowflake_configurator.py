"""Unit tests for ConfigureSnowflakeAssessment."""

import yaml

from databricks.labs.blueprint.tui import MockPrompts

from databricks.labs.lakebridge.assessments.configure_assessment import (
    ConfigureSnowflakeAssessment,
)


def _base_prompts(extra: dict | None = None) -> MockPrompts:
    """Happy-path prompt answers — extra overrides individual fields."""
    answers = {
        # Prompts.choice sorts options alphabetically; "env" is index 0, "local" is index 1.
        r"Enter secret vault type \(local \| env\)": str(sorted(["local", "env"]).index("env")),
        r"Enter Snowflake account URL.*": "myorg-myaccount.snowflakecomputing.com",
        r"Enter username": "TEST_USER",
        r"Enter warehouse name": "COMPUTE_WH",
        r"Enter database name": "SNOWFLAKE",
        r"Enter schema name": "ACCOUNT_USAGE",
        r"Enter role": "ACCOUNTADMIN",
        r"Enter Programmatic Access Token \(PAT\)": "fake-pat-token",
        r"Do you want to test the connection to snowflake\?": "no",
    }
    if extra:
        answers.update(extra)
    return MockPrompts(answers)


def test_configure_snowflake_credentials_writes_expected_yaml(tmp_path):
    cred_file = tmp_path / ".credentials.yml"
    assessment = ConfigureSnowflakeAssessment(
        product_name="lakebridge",
        source_name="snowflake",
        prompts=_base_prompts(),
        credential_file=cred_file,
    )
    assessment.run()

    with open(cred_file, encoding="utf-8") as file_handle:
        creds = yaml.safe_load(file_handle)

    assert creds == {
        "secret_vault_type": "env",
        "snowflake": {
            "connection": {
                "account": "myorg-myaccount.snowflakecomputing.com",
                "user": "TEST_USER",
                "warehouse": "COMPUTE_WH",
                "database": "SNOWFLAKE",
                "schema": "ACCOUNT_USAGE",
                "role": "ACCOUNTADMIN",
                "password": "fake-pat-token",
            },
        },
    }
