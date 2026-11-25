import logging
import re
from pathlib import Path

import pytest
import yaml
from databricks.labs.blueprint.tui import MockPrompts, Prompts

from databricks.labs.lakebridge.assessments import configure_assessment
from databricks.labs.lakebridge.assessments.configure_assessment import (
    ConfigureHadoopAssessment,
    create_assessment_configurator,
)
from databricks.labs.lakebridge.resources.assessments.hadoop import hadoop_config
from databricks.labs.lakebridge.resources.assessments.hadoop.ambari import AmbariError
from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import RestEndpoint
from databricks.labs.lakebridge.resources.assessments.hadoop.kerberos import KerberosSettings
from databricks.labs.lakebridge.resources.assessments.hadoop.masking import parse_masking_rules
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import HadoopApiError

# Questions every test answers with Enter unless it says otherwise: no masking, no YARN application filter, and the
# original's Impala defaults (the defaults apply when the answer is empty).
_DEFAULT_ANSWERS = {
    r"Mask data in the extract": "no",
    r"Filter the YARN applications": "",
    r"number of days the initial run reads Impala queries": "",
    r"pages of 1000 Impala queries": "",
}


def _configurator(tmp_path: Path, answers: dict[str, str]) -> ConfigureHadoopAssessment:
    return ConfigureHadoopAssessment(
        product_name="lakebridge",
        source_name="hadoop",
        prompts=MockPrompts({**_DEFAULT_ANSWERS, **answers}),
        credential_file=tmp_path / ".credentials.yml",
    )


class _MaskingPrompts(MockPrompts):
    """The configuration answers from a dict, and the masking questions from a script that is asked in order."""

    def __init__(self, answers: dict[str, str], masking: list[tuple[str, str]]) -> None:
        super().__init__({**_DEFAULT_ANSWERS, **answers})
        self._masking = list(masking)
        self.asked: list[tuple[str, str | None]] = []

    def question(self, text: str, default: str | None = None, **kwargs) -> str:
        self.asked.append((text, default))
        if self._masking and re.search(self._masking[0][0], text):
            _, answer = self._masking.pop(0)
            return answer or default or answer
        return super().question(text, default=default, **kwargs)


_HADOOP_ANSWERS = {
    r"Enter secret vault type": "1",
    r"Select the Hadoop distribution": "0",
    r"Enter the YARN ResourceManager URL": "http://localhost:8088",
    r"Select the YARN ResourceManager authentication": "0",
    r"Configure a Spark History Server": "no",
    r"Configure Cloudera Manager": "no",
    r"Configure Apache Ranger": "no",
    r"Read Impala daemons directly": "no",
    r"Configure Ambari": "no",
    r"Do you want to test the connection to hadoop": "no",
}


def _run_masking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, masking: list[tuple[str, str]]
) -> tuple[dict, _MaskingPrompts]:
    """Run the Hadoop configuration with HOME in tmp_path. Returns the saved hadoop section and the prompts asked."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    prompts = _MaskingPrompts(_HADOOP_ANSWERS, masking)
    ConfigureHadoopAssessment("lakebridge", prompts, "hadoop", tmp_path / ".credentials.yml").run()
    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    return credentials["hadoop"], prompts


def _default_rules(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".databricks" / "labs" / "lakebridge" / "hadoop-masking-rules.txt"


def test_no_masking_saves_no_rules_file_and_asks_nothing_more(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hadoop, prompts = _run_masking(tmp_path, monkeypatch, [(r"Mask data in the extract", "no")])

    assert "search_replace_file" not in hadoop
    assert not _default_rules(tmp_path).exists()
    assert not any("Domain" in text or "Redact" in text for text, _ in prompts.asked)


def test_an_existing_rules_file_is_checked_and_its_path_is_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = tmp_path / "my-rules.txt"
    rules.write_text("s/corp\\.example\\.com/masked.example/g\n", encoding="utf-8")
    masking = [
        (r"Mask data in the extract", "yes"),
        (r"Use an existing masking rules file", "yes"),
        (r"Path to the existing masking rules file", str(rules)),
    ]

    hadoop, prompts = _run_masking(tmp_path, monkeypatch, masking)

    assert hadoop["search_replace_file"] == str(rules)
    assert rules.read_text(encoding="utf-8") == "s/corp\\.example\\.com/masked.example/g\n"
    assert not any("Domain" in text for text, _ in prompts.asked)


def test_a_broken_existing_rules_file_is_reported_and_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    broken = tmp_path / "broken.txt"
    broken.write_text("s/a/b\n", encoding="utf-8")
    good = tmp_path / "good.txt"
    good.write_text("s/a/b/g\n", encoding="utf-8")
    masking = [
        (r"Mask data in the extract", "yes"),
        (r"Use an existing masking rules file", "yes"),
        (r"Path to the existing masking rules file", str(broken)),
        (r"Path to the existing masking rules file", str(good)),
    ]

    hadoop, _ = _run_masking(tmp_path, monkeypatch, masking)

    assert hadoop["search_replace_file"] == str(good)
    assert f"{broken}, line 1: the replacement has no closing /" in caplog.text


def test_a_missing_existing_rules_file_is_asked_for_ten_times_and_then_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    masking = [
        (r"Mask data in the extract", "yes"),
        (r"Use an existing masking rules file", "yes"),
        *[(r"Path to the existing masking rules file", str(tmp_path / "missing.txt"))] * 10,
    ]

    with pytest.raises(ValueError, match="No valid masking rules file was given in 10 attempts"):
        _run_masking(tmp_path, monkeypatch, masking)


def test_guided_answers_write_the_rules_file_owner_only_and_save_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    masking = [
        (r"Mask data in the extract", "yes"),
        (r"Use an existing masking rules file", "no"),
        (r"Domain\(s\) to mask in host names", "corp.example.com, eu.corp.example.com"),
        (r"Mask IP addresses", "yes"),
        (r"Redact user names", "yes"),
        (r"Redact query text and job names", "yes"),
    ]

    hadoop, prompts = _run_masking(tmp_path, monkeypatch, masking)

    rules = _default_rules(tmp_path)
    assert hadoop["search_replace_file"] == str(rules)
    assert rules.stat().st_mode & 0o777 == 0o600
    text = rules.read_text(encoding="utf-8")
    assert "s/\\.eu\\.corp\\.example\\.com\\>/.masked-2/g" in text
    assert "x.x." in text
    assert '"\\1":"redacted"' in text
    # Two domains (two rules each), seven IP rules (with version protect/restore), four user rules and four query rules.
    assert len(parse_masking_rules(text, str(rules))) == 4 + 7 + 4 + 4
    assert ("Domain(s) to mask in host names (comma-separated, blank to skip)", "none") in prompts.asked
    assert "Host names keep their own first label" in caplog.text
    assert "User names are redacted, so per-user workload analysis will not be possible." in caplog.text
    assert f"Saved masking rules to {rules}" in caplog.text


def test_guided_answers_with_blank_domains_and_no_redactions_write_a_header_only_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    masking = [
        (r"Mask data in the extract", "yes"),
        (r"Use an existing masking rules file", "no"),
        (r"Domain\(s\) to mask in host names", ""),
        (r"Mask IP addresses", "no"),
        (r"Redact user names", "no"),
        (r"Redact query text and job names", "no"),
    ]

    hadoop, _ = _run_masking(tmp_path, monkeypatch, masking)

    assert not parse_masking_rules(_default_rules(tmp_path).read_text(encoding="utf-8"), "rules")
    assert hadoop["search_replace_file"] == str(_default_rules(tmp_path))


def test_the_domain_question_asks_again_when_an_entry_is_not_a_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    masking = [
        (r"Mask data in the extract", "yes"),
        (r"Use an existing masking rules file", "no"),
        (r"Domain\(s\) to mask in host names", "not a domain!"),
        (r"Domain\(s\) to mask in host names", "corp.example.com"),
        (r"Mask IP addresses", "no"),
        (r"Redact user names", "no"),
        (r"Redact query text and job names", "no"),
    ]

    _run_masking(tmp_path, monkeypatch, masking)

    assert "Not a domain name: not a domain!" in caplog.text
    assert "s/\\<corp\\.example\\.com\\>/masked-1/g" in _default_rules(tmp_path).read_text(encoding="utf-8")


def test_an_existing_default_rules_file_is_kept_unless_overwriting_is_chosen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = _default_rules(tmp_path)
    rules.parent.mkdir(parents=True)
    rules.write_text("s/keep/me/g\n", encoding="utf-8")
    masking = [
        (r"Mask data in the extract", "yes"),
        (r"Use an existing masking rules file", "no"),
        (r"already exists. Overwrite it", "no"),
    ]

    hadoop, prompts = _run_masking(tmp_path, monkeypatch, masking)

    assert hadoop["search_replace_file"] == str(rules)
    assert rules.read_text(encoding="utf-8") == "s/keep/me/g\n"
    assert not any("Domain" in text for text, _ in prompts.asked)


def test_an_existing_default_rules_file_is_replaced_when_overwriting_is_chosen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = _default_rules(tmp_path)
    rules.parent.mkdir(parents=True)
    rules.write_text("s/keep/me/g\n", encoding="utf-8")
    masking = [
        (r"Mask data in the extract", "yes"),
        (r"Use an existing masking rules file", "no"),
        (r"already exists. Overwrite it", "yes"),
        (r"Domain\(s\) to mask in host names", "corp.example.com"),
        (r"Mask IP addresses", "no"),
        (r"Redact user names", "no"),
        (r"Redact query text and job names", "no"),
    ]

    _run_masking(tmp_path, monkeypatch, masking)

    assert "keep" not in rules.read_text(encoding="utf-8")
    assert rules.stat().st_mode & 0o777 == 0o600


def test_hadoop_is_a_registered_configurator(tmp_path: Path) -> None:
    configurator = create_assessment_configurator("hadoop", "lakebridge", MockPrompts({}), tmp_path / "c.yml")

    assert isinstance(configurator, ConfigureHadoopAssessment)


def test_configure_hadoop_with_ha_basic_auth_https_and_spark(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",  # local (choices are sorted: env, local)
        r"Select the Hadoop distribution": "2",  # cdh (the list is not sorted)
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
        r"Configure Ambari": "no",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials == {
        "secret_vault_type": "local",
        "secret_vault_name": None,
        "hadoop": {
            "distribution": "cdh",
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
        r"Configure Ambari": "no",
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
        r"Enter the YARN ResourceManager URL": "http://localhost:8088",
        r"Select the YARN ResourceManager authentication": "0",  # none
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
        r"Configure Ambari": "no",
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

    monkeypatch.setattr(hadoop_config, "RestClient", _Client)
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
        r"Configure Ambari": "no",
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
        # Enter takes the original profiler's defaults (CM_IMPALA_NUMBER_OF_DAYS=1, CM_IMPALA_PAGES=2).
        "impala_lookback_days": 1,
        "impala_max_pages": 2,
    }


def test_configure_cloudera_manager_without_impala_leaves_detection_to_the_steps(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",
        r"Select the Hadoop distribution": "2",
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
        r"Configure Ambari": "no",
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
        r"Configure Ambari": "no",
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
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
        r"Configure Ambari": "no",
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
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",  # none
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "yes",
        r"Configure Ambari": "no",
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
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
        r"Configure Ambari": "no",
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


def test_configure_yarn_kerberos_with_keytab_writes_the_block(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",  # local
        r"Select the Hadoop distribution": "2",  # cdh
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "2",  # kerberos
        r"Enter the path to the keytab for the YARN ResourceManager": "/etc/security/profiler.keytab",
        r"Enter the Kerberos principal for the YARN ResourceManager": "profiler@HADOOP.SIM",
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
        r"Configure Ambari": "no",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials["hadoop"]["yarn"] == {
        "urls": ["http://rm:8088"],
        "auth": "kerberos",
        "keytab": "/etc/security/profiler.keytab",
        "principal": "profiler@HADOOP.SIM",
    }


def test_configure_spark_history_kerberos_without_keytab_uses_the_existing_ticket(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",
        r"Select the Hadoop distribution": "2",
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",  # none
        r"Configure a Spark History Server": "yes",
        r"Enter the Spark History Server URL": "http://shs:18080",
        r"Select the Spark History Server authentication": "2",  # kerberos
        r"path to the keytab for the Spark History Server": "",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
        r"Configure Ambari": "no",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials["hadoop"]["spark_history"] == {"url": "http://shs:18080", "auth": "kerberos"}


def test_kerberos_yarn_check_connection_passes_the_endpoint_transport(tmp_path: Path, monkeypatch) -> None:
    seen: dict = {}

    def capture(urls, **kwargs):
        seen["urls"] = urls
        seen["make_client"] = kwargs.get("make_client")

    monkeypatch.setattr(configure_assessment, "find_active_resource_manager", capture)
    raw = {
        "distribution": "oth",
        "yarn": {"urls": ["http://rm:8088"], "auth": "kerberos", "principal": "profiler@HADOOP.SIM"},
    }

    _configurator(tmp_path, {})._check_connection(raw)  # pylint: disable=protected-access

    assert seen["urls"] == ("http://rm:8088",)
    # The factory is the endpoint's client method, bound to the endpoint with the Kerberos settings.
    assert seen["make_client"].__func__ is RestEndpoint.client
    assert seen["make_client"].__self__.kerberos == KerberosSettings(principal="profiler@HADOOP.SIM")


def test_configure_ambari_over_the_hdinsight_gateway_writes_the_block(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",  # local
        r"Select the Hadoop distribution": "3",  # hdp
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",  # none
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
        r"Configure Ambari": "yes",
        r"Enter the Ambari Server URL": "https://sim.azurehdinsight.net",
        r"Select the Ambari Server authentication": "0",  # none
        r"CA bundle for the Ambari Server": "",
        r"Verify the Ambari Server TLS certificate": "yes",
        r"Enter the Ambari cluster name": "",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials["hadoop"]["ambari"] == {
        "url": "https://sim.azurehdinsight.net",
        "auth": "none",
        "verify_ssl": True,
    }


def test_configure_ambari_with_basic_auth_and_a_named_cluster(tmp_path: Path) -> None:
    answers = {
        r"Enter secret vault type": "1",
        r"Select the Hadoop distribution": "3",
        r"Enter the YARN ResourceManager URL": "http://rm:8088",
        r"Select the YARN ResourceManager authentication": "0",
        r"Configure a Spark History Server": "no",
        r"Configure Cloudera Manager": "no",
        r"Configure Apache Ranger": "no",
        r"Read Impala daemons directly": "no",
        r"Configure Ambari": "yes",
        r"Enter the Ambari Server URL": "http://ambari.example.com:8080",
        r"Select the Ambari Server authentication": "1",  # basic
        r"Enter the Ambari Server username": "admin",
        r"Enter the Ambari Server password": "admin",
        r"Enter the Ambari cluster name": "Sandbox HDP",
        r"Do you want to test the connection to hadoop": "no",
    }
    _configurator(tmp_path, answers).run()

    credentials = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))
    assert credentials["hadoop"]["ambari"] == {
        "url": "http://ambari.example.com:8080",
        "auth": "basic",
        "username": "admin",
        "password": "admin",
        "cluster": "Sandbox HDP",
    }


def test_check_connection_resolves_the_configured_ambari_cluster(tmp_path: Path, monkeypatch) -> None:
    resolved: list[tuple[str, str | None]] = []

    def resolve(client, configured):
        resolved.append((client.base_url, configured))
        return "sim"

    monkeypatch.setattr(configure_assessment, "resolve_ambari_cluster", resolve)
    raw = {
        "distribution": "hdp",
        "ambari": {"url": "https://sim.azurehdinsight.net/", "auth": "none", "cluster": "sim"},
    }

    _configurator(tmp_path, {})._check_connection(raw)  # pylint: disable=protected-access

    assert resolved == [("https://sim.azurehdinsight.net", "sim")]


def test_check_connection_reports_an_ambari_failure_as_a_connection_error(tmp_path: Path, monkeypatch) -> None:
    def several(*_args, **_kwargs):
        raise AmbariError("Ambari manages 2 clusters; set hadoop.ambari.cluster to one of: 'prod', 'dev'")

    monkeypatch.setattr(configure_assessment, "resolve_ambari_cluster", several)
    raw = {"distribution": "hdp", "ambari": {"url": "http://ambari:8080", "auth": "none"}}

    with pytest.raises(ConnectionError, match="manages 2 clusters"):
        _configurator(tmp_path, {})._check_connection(raw)  # pylint: disable=protected-access


def test_optional_questions_accept_enter_with_the_real_prompts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """blueprint's real Prompts re-ask a blank answer unless the default is non-empty, which MockPrompts does not do.
    Every optional question ("leave empty to ...") must take Enter as "leave it out"."""
    script = [  # (prompt pattern, typed answer); "" is pressing Enter
        (r"Enter secret vault type", "1"),
        (r"Select the Hadoop distribution", "2"),
        (r"Enter the YARN ResourceManager URL", "https://rm.example.com:8090"),
        (r"Select the YARN ResourceManager authentication", "0"),
        (r"CA bundle for the YARN ResourceManager", ""),
        (r"Verify the YARN ResourceManager TLS certificate", "yes"),
        (r"Filter the YARN applications", ""),
        (r"Configure a Spark History Server", "yes"),
        (r"Enter the Spark History Server URL", "http://shs.example.com:18081"),
        (r"Select the Spark History Server authentication", "2"),
        (r"keytab for the Spark History Server", ""),
        (r"Configure Cloudera Manager", "yes"),
        (r"Enter the Cloudera Manager URL", "http://cm.example.com:7180"),
        (r"Select the Cloudera Manager authentication", "0"),
        (r"Cloudera Manager cluster name", ""),
        (r"Extract Impala queries", "yes"),
        (r"Impala query interval", "0"),
        (r"number of days the initial run reads Impala queries", ""),
        (r"pages of 1000 Impala queries", ""),
        (r"Impala service name", ""),
        (r"Configure Apache Ranger", "no"),
        (r"Read Impala daemons directly", "yes"),
        (r"Enter the Impala daemon URL", "http://impalad.example.com:25000"),
        (r"Select the Impala daemon authentication", "0"),
        (r"Impala catalogd web UI URL", ""),
        (r"Impala statestore web UI URL", ""),
        (r"Configure Ambari", "yes"),
        (r"Enter the Ambari Server URL", "http://ambari.example.com:8080"),
        (r"Select the Ambari Server authentication", "0"),
        (r"Ambari cluster name", ""),
        (r"Mask data in the extract", "yes"),
        (r"Use an existing masking rules file", "no"),
        (r"Domain\(s\) to mask", ""),
        (r"Mask IP addresses", "no"),
        (r"Redact user names", "no"),
        (r"Redact query text", "no"),
    ]
    shown: list[str] = []

    def typed(prompt: str = "") -> str:
        plain = re.sub(r"\x1b\[[0-9;]*m", "", prompt)
        shown.append(plain)
        pattern, answer = script[0]
        assert re.search(pattern, plain), f"expected {pattern!r}, asked {plain!r}"
        script.pop(0)
        return answer

    monkeypatch.setattr("builtins.input", typed)
    monkeypatch.setattr(configure_assessment, "default_rules_file", lambda: tmp_path / "rules.txt")
    creds = tmp_path / ".credentials.yml"
    configurator = ConfigureHadoopAssessment("lakebridge", Prompts(), "hadoop", creds)
    configurator._configure_credentials()  # pylint: disable=protected-access

    assert not script, f"not asked: {script}"
    saved = yaml.safe_load(creds.read_text())["hadoop"]
    assert saved["yarn"]["verify_ssl"] is True  # no CA bundle: the system trust store
    assert "keytab" not in saved["spark_history"] and saved["spark_history"]["auth"] == "kerberos"
    assert "cluster" not in saved["cloudera_manager"] and "impala_service" not in saved["cloudera_manager"]
    assert "catalogd_url" not in saved["impala"] and "statestored_url" not in saved["impala"]
    assert "cluster" not in saved["ambari"]
    assert "app_filter" not in saved["yarn"]
    assert (saved["cloudera_manager"]["impala_lookback_days"], saved["cloudera_manager"]["impala_max_pages"]) == (1, 2)
    optional = [text for text in shown if "leave empty" in text or "blank to skip" in text]
    assert len(optional) == 9 and all("(default: none)" in text for text in optional)


def test_the_original_profiler_settings_are_asked_and_saved(tmp_path: Path) -> None:
    """RM_APP_FILTER, CM_IMPALA_NUMBER_OF_DAYS and CM_IMPALA_PAGES, as the original profiler.conf sets them."""
    answers = {
        **_HADOOP_ANSWERS,
        r"Select the Hadoop distribution": "2",  # cdh
        r"Filter the YARN applications": "?user=etl&queue=root.prod",
        r"Configure Cloudera Manager": "yes",
        r"Enter the Cloudera Manager URL": "http://cm.example.net:7180",
        r"Select the Cloudera Manager authentication": "0",
        r"Enter the Cloudera Manager cluster name": "",
        r"Extract Impala queries": "yes",
        r"Select the Impala query interval": "5",  # 20 minutes
        r"number of days the initial run reads Impala queries": "7",
        r"pages of 1000 Impala queries": "5",
        r"Enter the Impala service name": "",
    }
    _configurator(tmp_path, answers).run()

    saved = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))["hadoop"]
    assert saved["yarn"]["app_filter"] == "user=etl&queue=root.prod"
    cloudera = saved["cloudera_manager"]
    assert (cloudera["impala_interval_minutes"], cloudera["impala_lookback_days"], cloudera["impala_max_pages"]) == (
        20,
        7,
        5,
    )
    parsed = hadoop_config.parse_hadoop_config(saved)
    assert parsed.yarn is not None and parsed.yarn.app_filter == (("user", "etl"), ("queue", "root.prod"))


class _SequencePrompts(MockPrompts):
    """Answers one question pattern from a list, in order, so a re-asked question gets the next answer."""

    def __init__(self, answers: dict[str, str], pattern: str, sequence: list[str]) -> None:
        super().__init__({**_DEFAULT_ANSWERS, **answers})
        self._pattern, self._sequence = pattern, list(sequence)

    def question(self, text: str, default: str | None = None, **kwargs) -> str:
        if re.search(self._pattern, text) and self._sequence:
            return self._sequence.pop(0)
        return super().question(text, default=default, **kwargs)


def test_an_invalid_app_filter_is_reported_and_asked_again(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    prompts = _SequencePrompts(_HADOOP_ANSWERS, r"Filter the YARN applications", ["user", "states=FINISHED"])
    ConfigureHadoopAssessment("lakebridge", prompts, "hadoop", tmp_path / ".credentials.yml").run()

    saved = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))["hadoop"]
    assert saved["yarn"]["app_filter"] == "states=FINISHED"
    assert "app_filter is not a valid query string" in caplog.text


def test_impala_days_and_pages_out_of_range_are_asked_again(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    answers = {
        **_HADOOP_ANSWERS,
        r"Configure Cloudera Manager": "yes",
        r"Enter the Cloudera Manager URL": "http://cm.example.net:7180",
        r"Select the Cloudera Manager authentication": "0",
        r"Enter the Cloudera Manager cluster name": "",
        r"Extract Impala queries": "yes",
        r"Select the Impala query interval": "7",
        r"pages of 1000 Impala queries": "",
        r"Enter the Impala service name": "",
    }
    prompts = _SequencePrompts(answers, r"number of days the initial run reads Impala queries", ["0", "4000", "3"])
    ConfigureHadoopAssessment("lakebridge", prompts, "hadoop", tmp_path / ".credentials.yml").run()

    saved = yaml.safe_load((tmp_path / ".credentials.yml").read_text(encoding="utf-8"))["hadoop"]
    assert saved["cloudera_manager"]["impala_lookback_days"] == 3
    assert caplog.text.count("Enter a whole number from 1 to 3650") == 2
