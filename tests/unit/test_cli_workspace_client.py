import os
from pathlib import Path

import pytest

from databricks.labs.lakebridge import cli

_HOST = "https://example.gcp.databricks.com"
_AUDIENCE = f"{_HOST}/oidc/v1/token"


@pytest.fixture(autouse=True)
def isolated_databricks_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Start each test without any Databricks configuration from the developer's environment."""
    for name in list(os.environ):
        if name.startswith("DATABRICKS_") or name.startswith("ACTIONS_ID_TOKEN_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(tmp_path / "missing.databrickscfg"))


def test_workspace_client_with_pat_ignores_token_audience_from_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Databricks CLI passes DATABRICKS_TOKEN_AUDIENCE alongside a PAT; this must not be seen as a second auth method."""
    monkeypatch.setenv("DATABRICKS_HOST", _HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-not-a-real-token")
    monkeypatch.setenv("DATABRICKS_TOKEN_AUDIENCE", _AUDIENCE)

    ws = cli.lakebridge.create_workspace_client()

    assert ws.config.auth_type == "pat"


def test_token_audience_dropped_for_oauth_m2m(monkeypatch: pytest.MonkeyPatch) -> None:
    """OAuth M2M (client ID and secret) doesn't use a token audience, so the one passed by the CLI is dropped."""
    monkeypatch.setenv("DATABRICKS_HOST", _HOST)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "service-principal-id")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "not-a-real-secret")
    monkeypatch.setenv("DATABRICKS_TOKEN_AUDIENCE", _AUDIENCE)

    cli.lakebridge.drop_conflicting_token_audience()

    assert "DATABRICKS_TOKEN_AUDIENCE" not in os.environ


def test_token_audience_kept_for_oidc_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """OIDC flows (e.g. GitHub OIDC) use a client ID with the audience, so it is left alone."""
    monkeypatch.setenv("DATABRICKS_HOST", _HOST)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "github-oidc-client")
    monkeypatch.setenv("DATABRICKS_TOKEN_AUDIENCE", _AUDIENCE)

    cli.lakebridge.drop_conflicting_token_audience()

    assert os.environ["DATABRICKS_TOKEN_AUDIENCE"] == _AUDIENCE


def test_token_audience_kept_when_auth_type_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit auth type already resolves the conflict, so the environment is left as configured."""
    monkeypatch.setenv("DATABRICKS_HOST", _HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-not-a-real-token")
    monkeypatch.setenv("DATABRICKS_TOKEN_AUDIENCE", _AUDIENCE)
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "pat")

    cli.lakebridge.drop_conflicting_token_audience()

    assert os.environ["DATABRICKS_TOKEN_AUDIENCE"] == _AUDIENCE
