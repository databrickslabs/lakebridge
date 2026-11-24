from pathlib import Path

import pytest
import yaml
from databricks.labs.blueprint.tui import MockPrompts

from databricks.labs.lakebridge.assessments import configure_assessment
from databricks.labs.lakebridge.assessments.configure_assessment import (
    ConfigureHadoopAssessment,
    create_assessment_configurator,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import HadoopApiError


def _configurator(tmp_path: Path, answers: dict[str, str]) -> ConfigureHadoopAssessment:
    return ConfigureHadoopAssessment(
        product_name="lakebridge",
        source_name="hadoop",
        prompts=MockPrompts(answers),
        credential_file=tmp_path / ".credentials.yml",
    )


def test_hadoop_is_a_registered_configurator(tmp_path: Path) -> None:
    configurator = create_assessment_configurator("hadoop", "lakebridge", MockPrompts({}), tmp_path / "c.yml")

    assert isinstance(configurator, ConfigureHadoopAssessment)


def test_configure_hadoop_with_ha_basic_auth_https_and_spark(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",  # local (choices are sorted: env, local)
        r"Select the Hadoop distribution": "2",  # cdh (the list is not sorted)
        r"Enter the lookback window in days": "30",
        r"Enter the YARN ResourceManager URL": "https://rm1.example.net/yarnui, https://rm2.example.net/yarnui",
        r"Select the YARN ResourceManager authentication": "1",  # basic
        r"Enter the YARN ResourceManager username": "profiler",
        r"Enter the YARN ResourceManager password": "s3cret",
        r"CA bundle for the YARN ResourceManager": "",
        r"Verify the YARN ResourceManager TLS certificate": "yes",
        r"Configure a Spark History Server": "yes",
        r"Enter the Spark History Server URL": "http://spark-hs:18081",
        r"Select the Spark History Server authentication": "0",  # none
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials == {
        "secret_vault_type": "local",
        "secret_vault_name": None,
        "hadoop": {
            "distribution": "cdh",
            "lookback_days": 30,
            "yarn": {
                "urls": ["https://rm1.example.net/yarnui", "https://rm2.example.net/yarnui"],
                "auth": "basic",
                "username": "profiler",
                "password": "s3cret",
                "verify_ssl": True,
            },
            "spark_history": {"url": "http://spark-hs:18081", "auth": "none"},
        },
    }


def test_configure_hadoop_env_vault_stores_variable_names_and_a_ca_bundle(tmp_path: Path) -> None:
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("synthetic", encoding="utf-8")
    answers = {
        r"Enter secret vault type": "0",  # env
        r"Select the Hadoop distribution": "4",  # hdi
        r"Enter the lookback window in days": "14",
        r"Enter the YARN ResourceManager URL": "https://c.azurehdinsight.net/yarnui",
        r"Select the YARN ResourceManager authentication": "1",
        r"Enter the YARN ResourceManager username": "admin",
        r"Enter the environment variable name holding the YARN ResourceManager password": "HDI_PASSWORD",
        r"CA bundle for the YARN": str(ca_bundle),
        r"Verify the YARN ResourceManager TLS certificate": "yes",
        r"Configure a Spark History Server": "no",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials["secret_vault_type"] == "env"
    assert credentials["hadoop"]["distribution"] == "hdi"
    assert credentials["hadoop"]["yarn"] == {
        "urls": ["https://c.azurehdinsight.net/yarnui"],
        "auth": "basic",
        "username": "admin",
        "password": "HDI_PASSWORD",
        "verify_ssl": str(ca_bundle),
    }
    assert "spark_history" not in credentials["hadoop"]


def test_configure_hadoop_plain_http_without_auth_asks_no_tls_questions(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",
        r"Select the Hadoop distribution": "0",  # oth
        r"Enter the lookback window in days": "30",
        r"Enter the YARN ResourceManager URL": "http://localhost:8088",
        r"Select the YARN ResourceManager authentication": "0",  # none
        r"Configure a Spark History Server": "no",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials["hadoop"]["yarn"] == {"urls": ["http://localhost:8088"], "auth": "none"}


def test_check_connection_reports_a_failed_rm_lookup_as_a_connection_error(tmp_path: Path, monkeypatch) -> None:
    def no_active(*_args, **_kwargs):
        raise HadoopApiError("No ACTIVE ResourceManager found: rm:8088 reports haState 'STANDBY'")

    monkeypatch.setattr(configure_assessment, "find_active_resource_manager", no_active)
    raw = {"distribution": "oth", "yarn": {"urls": ["http://rm:8088"], "auth": "none"}}

    with pytest.raises(ConnectionError, match="No ACTIVE ResourceManager"):
        _configurator(tmp_path, {})._check_connection(raw)  # pylint: disable=protected-access


def test_check_connection_probes_the_spark_history_server(tmp_path: Path, monkeypatch) -> None:
    probed: list[tuple] = []

    class _Client:
        def __init__(self, url, **_kwargs):
            self._url = url

        def get_json(self, path, params=None):
            probed.append((self._url, path, params))
            return []

    monkeypatch.setattr(configure_assessment, "RestClient", _Client)
    monkeypatch.setattr(configure_assessment, "find_active_resource_manager", lambda *_a, **_k: None)
    raw = {
        "distribution": "oth",
        "yarn": {"urls": ["http://rm:8088"], "auth": "none"},
        "spark_history": {"url": "http://spark:18081", "auth": "none"},
    }

    _configurator(tmp_path, {})._check_connection(raw)  # pylint: disable=protected-access

    assert probed == [("http://spark:18081", "/api/v1/applications", {"limit": 1})]
