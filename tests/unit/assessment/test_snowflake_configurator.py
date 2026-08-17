"""Unit tests for ConfigureSnowflakeAssessment."""

import yaml
from databricks.labs.blueprint.tui import MockPrompts

from databricks.labs.lakebridge.assessments.configure_assessment import (
    ConfigureSnowflakeAssessment,
)
from databricks.labs.lakebridge.connections.snowflake_auth import AUTH_CHOICES, KeyPair, Pat

# Prompts.choice sorts options alphabetically; "env" is index 0, "local" is index 1.
_VAULT_INDEX = {vault: idx for idx, vault in enumerate(sorted(["local", "env"]))}
# Authentication method uses sort=True; index matches sorted labels.
_AUTH_INDEX = {label: str(i) for i, label in enumerate(sorted(c.label for c in AUTH_CHOICES))}

_SHARED_CONNECTION_PROMPTS = {
    r"Enter Snowflake account URL.*": "myorg-myaccount.snowflakecomputing.com",
    r"Enter username": "TEST_USER",
    r"Enter warehouse name": "COMPUTE_WH",
    r"Enter database name": "SNOWFLAKE",
    r"Enter schema name": "ACCOUNT_USAGE",
    r"Enter role": "ACCOUNTADMIN",
    r"Do you want to test the connection to snowflake\?": "no",
}


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


def _pat_prompts(vault_type: str, pat_prompt: str, pat_answer: str) -> MockPrompts:
    return MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": str(_VAULT_INDEX[vault_type]),
            r"Select authentication method": _AUTH_INDEX[Pat.label],
            **_SHARED_CONNECTION_PROMPTS,
            pat_prompt: pat_answer,
        }
    )


def _key_pair_prompts(
    *,
    encrypted: bool,
    passphrase: str | None = None,
    vault_type: str = "local",
) -> MockPrompts:
    answers: dict[str, str] = {
        r"Enter secret vault type \(local \| env\)": str(_VAULT_INDEX[vault_type]),
        r"Select authentication method": _AUTH_INDEX[KeyPair.label],
        **_SHARED_CONNECTION_PROMPTS,
        r"Enter path to the private key file.*": "/path/to/rsa_key.p8",
        r"Is the private key encrypted with a passphrase\?": "yes" if encrypted else "no",
    }
    if encrypted:
        answers[r"Enter private key passphrase"] = "" if passphrase is None else passphrase
    return MockPrompts(answers)


def test_local_vault_stores_pat_verbatim(tmp_path):
    prompts = _pat_prompts("local", r"Enter Programmatic Access Token \(PAT\)", "fake-pat-token")
    creds = _run(prompts, tmp_path)
    assert creds == {
        "secret_vault_type": "local",
        "snowflake": {
            "connection": {
                "auth_type": "pat",
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
    prompts = _pat_prompts("env", r"Enter the environment variable name holding the PAT", "SNOWFLAKE_PAT")
    creds = _run(prompts, tmp_path)
    assert creds["secret_vault_type"] == "env"
    assert creds["snowflake"]["connection"]["pat"] == "SNOWFLAKE_PAT"
    assert creds["snowflake"]["connection"]["auth_type"] == "pat"


def test_local_vault_stores_key_pair_without_passphrase(tmp_path):
    creds = _run(_key_pair_prompts(encrypted=False), tmp_path)
    connection = creds["snowflake"]["connection"]
    assert connection["auth_type"] == "key_pair"
    assert connection["private_key_path"] == "/path/to/rsa_key.p8"
    assert "private_key_passphrase" not in connection
    assert "pat" not in connection


def test_local_vault_stores_key_pair_with_passphrase(tmp_path):
    creds = _run(_key_pair_prompts(encrypted=True, passphrase="secret-pass"), tmp_path)
    connection = creds["snowflake"]["connection"]
    assert connection["auth_type"] == "key_pair"
    assert connection["private_key_path"] == "/path/to/rsa_key.p8"
    assert connection["private_key_passphrase"] == "secret-pass"


def test_local_vault_stores_key_pair_with_empty_passphrase(tmp_path):
    creds = _run(_key_pair_prompts(encrypted=True, passphrase=""), tmp_path)
    connection = creds["snowflake"]["connection"]
    assert connection["auth_type"] == "key_pair"
    assert connection["private_key_path"] == "/path/to/rsa_key.p8"
    assert connection["private_key_passphrase"] == ""
