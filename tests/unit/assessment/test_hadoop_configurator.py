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
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
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
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
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
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
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


def test_configure_cloudera_manager_with_impala_writes_the_block(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",  # local
        r"Select the Hadoop distribution": "2",  # cdh
        r"Enter the lookback window in days": "30",
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",  # none
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "yes",
        r"Configure Apache Ranger": "no",
        r"Enter the Cloudera Manager URL": "https://cm.example.net:7183",
        r"Select the Cloudera Manager authentication": "1",  # basic
        r"Enter the Cloudera Manager username": "profiler",
        r"Enter the Cloudera Manager password": "s3cret",
        r"CA bundle for the Cloudera Manager": "",
        r"Verify the Cloudera Manager TLS certificate": "yes",
        r"Enter the Cloudera Manager cluster name": "Cloudera QuickStart",
        r"Extract Impala queries": "yes",
        r"Select the Impala query interval": "7",  # 60 minutes (the options are 1, 2, 3, 6, 10, 20, 30, 60)
        r"Enter the Impala service name": "",
        r"Read Impala daemons directly": "no",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials["hadoop"]["cloudera_manager"] == {
        "url": "https://cm.example.net:7183",
        "auth": "basic",
        "username": "profiler",
        "password": "s3cret",
        "verify_ssl": True,
        "cluster": "Cloudera QuickStart",
        "extract_impala_queries": True,
        "impala_interval_minutes": 60,
    }


def test_configure_cloudera_manager_without_impala_leaves_detection_to_the_steps(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",
        r"Select the Hadoop distribution": "2",
        r"Enter the lookback window in days": "30",
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "yes",
        r"Configure Apache Ranger": "no",
        r"Enter the Cloudera Manager URL": "http://cm:7180",
        r"Select the Cloudera Manager authentication": "0",  # none
        r"Enter the Cloudera Manager cluster name": "",
        r"Extract Impala queries": "no",
        r"Read Impala daemons directly": "no",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials["hadoop"]["cloudera_manager"] == {"url": "http://cm:7180", "auth": "none"}


def test_check_connection_fails_when_cm_manages_several_clusters(tmp_path: Path, monkeypatch) -> None:
    class _Cm:
        def __init__(self, url, **_kwargs):
            self._url = url

        def get_plain_text(self, _path, _params=None):
            return "v12"

        def get_json(self, _path, _params=None):
            return {"items": [{"name": "prod"}, {"name": "dev"}]}

    monkeypatch.setattr(configure_assessment, "RestClient", _Cm)
    monkeypatch.setattr(configure_assessment, "find_active_resource_manager", lambda *_a, **_k: None)
    raw = {
        "distribution": "cdh",
        "yarn": {"urls": ["http://rm:8088"], "auth": "none"},
        "cloudera_manager": {"url": "http://cm:7180", "auth": "none"},
    }

    with pytest.raises(ConnectionError, match="manages 2 clusters"):
        _configurator(tmp_path, {})._check_connection(raw)  # pylint: disable=protected-access


def test_check_connection_resolves_the_configured_impala_service(tmp_path: Path, monkeypatch) -> None:
    seen: list[str] = []

    class _Cm:
        def __init__(self, url, **_kwargs):
            pass

        def get_plain_text(self, _path, _params=None):
            return "v12"

        def get_json(self, path, _params=None):
            seen.append(path)
            if path.endswith("/services"):
                return {"items": [{"name": "impala", "type": "IMPALA"}]}
            return {"items": [{"name": "Cloudera QuickStart"}]}

    monkeypatch.setattr(configure_assessment, "RestClient", _Cm)
    raw = {
        "distribution": "cdh",
        "cloudera_manager": {"url": "http://cm:7180", "auth": "none", "extract_impala_queries": True},
    }

    _configurator(tmp_path, {})._check_connection(raw)  # pylint: disable=protected-access

    assert seen == ["/api/v12/clusters", "/api/v12/clusters/Cloudera%20QuickStart/services"]


def test_configure_hadoop_with_ranger_writes_the_block(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",  # local
        r"Select the Hadoop distribution": "3",  # hdp (the list is not sorted)
        r"Enter the lookback window in days": "30",
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",  # none
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "yes",
        r"Enter the Apache Ranger URL": "https://ranger.example.net:6182",
        r"Select the Apache Ranger authentication": "1",  # basic
        r"Enter the Apache Ranger username": "admin",
        r"Enter the Apache Ranger password": "s3cret",
        r"CA bundle for the Apache Ranger": "",
        r"Verify the Apache Ranger TLS certificate": "yes",
        r"Read Impala daemons directly": "no",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials["hadoop"]["ranger"] == {
        "url": "https://ranger.example.net:6182",
        "auth": "basic",
        "username": "admin",
        "password": "s3cret",
        "verify_ssl": True,
    }
    assert "spark_history" not in credentials["hadoop"]
    assert "cloudera_manager" not in credentials["hadoop"]


def test_configure_hadoop_without_ranger_writes_no_ranger_block(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",
        r"Select the Hadoop distribution": "3",
        r"Enter the lookback window in days": "30",
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert "ranger" not in credentials["hadoop"]


def test_check_connection_reads_the_ranger_service_list(tmp_path: Path, monkeypatch) -> None:
    seen: list[str] = []

    def list_services(client, **_kwargs):
        seen.append(client.base_url)
        return []

    monkeypatch.setattr(configure_assessment, "find_active_resource_manager", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(configure_assessment, "list_services", list_services)
    raw = {
        "distribution": "hdp",
        "yarn": {"urls": ["http://rm:8088"], "auth": "none"},
        "ranger": {"url": "https://ranger:6182/", "auth": "none"},
    }

    _configurator(tmp_path, {})._check_connection(raw)  # pylint: disable=protected-access

    assert seen == ["https://ranger:6182"]


def test_check_connection_reports_a_ranger_failure_as_a_connection_error(tmp_path: Path, monkeypatch) -> None:
    def refused(*_args, **_kwargs):
        raise HadoopApiError("GET /service/public/v2/api/service from https://ranger:6182 returned HTTP 401")

    monkeypatch.setattr(configure_assessment, "find_active_resource_manager", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(configure_assessment, "list_services", refused)
    raw = {
        "distribution": "hdp",
        "yarn": {"urls": ["http://rm:8088"], "auth": "none"},
        "ranger": {"url": "https://ranger:6182", "auth": "none"},
    }

    with pytest.raises(ConnectionError, match="HTTP 401"):
        _configurator(tmp_path, {})._check_connection(raw)  # pylint: disable=protected-access


def test_configure_hadoop_with_impala_writes_the_block(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",  # local
        r"Select the Hadoop distribution": "0",  # oth (the list is not sorted)
        r"Enter the lookback window in days": "30",
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",  # none
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "yes",
        r"Enter the Impala daemon URL": "http://impalad-a:25000, http://impalad-b:25000",
        r"Select the Impala daemon authentication": "0",  # none
        r"Enter the Impala catalogd web UI URL": "http://catalogd:25020",
        r"Enter the Impala statestore web UI URL": "",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials["hadoop"]["impala"] == {
        "urls": ["http://impalad-a:25000", "http://impalad-b:25000"],
        "auth": "none",
        "catalogd_url": "http://catalogd:25020",
    }


def test_configure_hadoop_without_impala_writes_no_impala_block(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",
        r"Select the Hadoop distribution": "0",
        r"Enter the lookback window in days": "30",
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert "impala" not in credentials["hadoop"]


def test_check_connection_probes_every_impalad_and_the_services(tmp_path: Path, monkeypatch) -> None:
    seen: list[str] = []

    class _Recorder:
        def __init__(self, base_url: str, **_kwargs) -> None:
            self.base_url = base_url

        def get_json(self, path: str, *_args, **_kwargs):
            seen.append(f"{self.base_url}{path}")
            return {}

    monkeypatch.setattr(configure_assessment, "RestClient", _Recorder)
    raw = {
        "distribution": "oth",
        "impala": {
            "urls": ["http://impalad-a:25000", "http://impalad-b:25000/"],
            "auth": "none",
            "catalogd_url": "http://catalogd:25020",
            "statestored_url": "http://statestored:25010",
        },
    }

    _configurator(tmp_path, {})._check_connection(raw)  # pylint: disable=protected-access

    assert seen == [
        "http://impalad-a:25000/backends?json",
        "http://impalad-b:25000/backends?json",
        "http://catalogd:25020/catalog?json",
        "http://statestored:25010/subscribers?json",
    ]


def test_check_connection_reports_an_impala_failure_as_a_connection_error(tmp_path: Path, monkeypatch) -> None:
    def refused(*_args, **_kwargs):
        raise HadoopApiError("GET /backends?json from http://impalad-a:25000 timed out")

    class _Down:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_json(self, *_args, **_kwargs):
            return refused()

    monkeypatch.setattr(configure_assessment, "RestClient", _Down)
    raw = {"distribution": "oth", "impala": {"urls": ["http://impalad-a:25000"], "auth": "none"}}

    with pytest.raises(ConnectionError, match="timed out"):
        _configurator(tmp_path, {})._check_connection(raw)  # pylint: disable=protected-access
