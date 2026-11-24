from pathlib import Path
from typing import Any

import pytest
import yaml

from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import (
    HadoopConfigError,
    load_hadoop_config,
    parse_hadoop_config,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.kerberos import KerberosSettings

_YARN = {"urls": ["http://rm1:8088", "http://rm2:8088"], "auth": "none", "verify_ssl": True}


def _raw(**overrides: object) -> dict:
    raw: dict = {"distribution": "cdh", "yarn": dict(_YARN)}
    raw.update(overrides)
    return raw


def test_minimal_config_gets_defaults() -> None:
    config = parse_hadoop_config(_raw())

    assert config.distribution == "cdh"
    assert config.spark_history is None
    assert config.yarn is not None
    assert config.yarn.urls == ("http://rm1:8088", "http://rm2:8088")
    assert config.yarn.username is None
    assert config.yarn.verify is True


def test_app_filter_and_mask_rules_are_optional_and_parsed() -> None:
    assert not parse_hadoop_config(_raw()).mask_rules

    raw = _raw(
        yarn={**_YARN, "app_filter": "?user=cloudera&queue=root.etl"},
        mask_rules=[{"pattern": r"internal\.example\.com", "replacement": "masked"}, {"pattern": "secret"}],
    )
    config = parse_hadoop_config(raw)

    assert config.yarn is not None
    assert config.yarn.app_filter == (("user", "cloudera"), ("queue", "root.etl"))
    assert config.mask("host1.internal.example.com has a secret") == "host1.masked has a "


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"yarn": {**_YARN, "app_filter": 5}}, "app_filter must be a query string"),
        ({"yarn": {**_YARN, "app_filter": "user"}}, "app_filter is not a valid query string"),
        ({"mask_rules": {"pattern": "x"}}, "mask_rules must be a list"),
        ({"mask_rules": [{"replacement": "x"}]}, r"mask_rules\[0\] needs a non-empty 'pattern'"),
        ({"mask_rules": [{"pattern": "("}]}, r"mask_rules\[0\].pattern is not a valid regex"),
        ({"mask_rules": [{"pattern": "x", "replacement": 1}]}, r"mask_rules\[0\].replacement must be text"),
    ],
)
def test_invalid_app_filter_or_mask_rules_are_rejected(overrides: dict, message: str) -> None:
    with pytest.raises(HadoopConfigError, match=message):
        parse_hadoop_config(_raw(**overrides))


def test_basic_auth_is_inferred_from_a_username() -> None:
    raw = _raw(yarn={"urls": ["https://c.example.net/yarnui"], "username": "profiler", "password": "pw"})

    yarn = parse_hadoop_config(raw).yarn

    assert yarn is not None
    assert (yarn.username, yarn.password) == ("profiler", "pw")


def test_gateway_url_keeps_its_path() -> None:
    raw = _raw(yarn={"urls": ["https://c.azurehdinsight.net/yarnui/"], "username": "admin", "password": "pw"})

    assert parse_hadoop_config(raw).yarn.urls == ("https://c.azurehdinsight.net/yarnui",)  # type: ignore[union-attr]


def test_yarn_urls_may_be_a_comma_separated_string() -> None:
    raw = _raw(yarn={"urls": "http://rm1:8088, http://rm2:8088"})

    assert parse_hadoop_config(raw).yarn.urls == ("http://rm1:8088", "http://rm2:8088")  # type: ignore[union-attr]


def test_spark_history_block_is_parsed_with_its_own_settings() -> None:
    raw = _raw(spark_history={"url": "https://spark-hs:18081", "username": "u", "password": "p", "verify_ssl": "false"})

    spark = parse_hadoop_config(raw).spark_history

    assert spark is not None
    assert spark.urls == ("https://spark-hs:18081",)
    assert (spark.username, spark.password, spark.verify) == ("u", "p", False)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"yarn": dict(_YARN)}, "hadoop.distribution must be one of"),
        (_raw(distribution="cloudera"), "hadoop.distribution must be one of"),
        (_raw(yarn={"urls": []}), "hadoop.yarn.urls must be a non-empty list"),
        (_raw(yarn={"urls": ["rm1:8088"]}), "Invalid Hadoop service URL"),
        (_raw(yarn={"urls": ["http://u:p@rm1:8088"]}), "put credentials in username and password"),
        (_raw(yarn={"urls": ["http://rm1:8088"], "auth": "ntlm"}), "auth must be one of none, basic"),
        (_raw(yarn={"urls": ["http://rm1:8088"], "auth": "basic"}), "auth 'basic' needs a username"),
        (_raw(yarn={"urls": ["http://rm1:8088"], "auth": "none", "username": "u"}), "auth 'none' takes no"),
        (_raw(yarn={"urls": ["http://rm1:8088"], "verify_ssl": ""}), "verify_ssl must be true, false"),
        (_raw(yarn={"urls": ["http://rm1:8088"], "verify_ssl": "/no/such/ca.pem"}), "CA bundle not found"),
        (_raw(spark_history={"url": ""}), "hadoop.spark_history.url is required"),
        (_raw(spark_history="http://spark:18081"), "hadoop.spark_history must be a mapping"),
    ],
)
def test_invalid_config_is_rejected(raw: dict, message: str) -> None:
    with pytest.raises(HadoopConfigError, match=message):
        parse_hadoop_config(raw)


def test_verify_ssl_accepts_an_existing_ca_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("synthetic", encoding="utf-8")
    raw = _raw(yarn={"urls": ["https://rm1:8090"], "verify_ssl": str(bundle)})

    assert parse_hadoop_config(raw).yarn.verify == str(bundle)  # type: ignore[union-attr]


def test_load_hadoop_config_reads_the_hadoop_section(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.yml"
    creds.write_text(
        yaml.safe_dump(
            {
                "secret_vault_type": "local",
                "hadoop": {"distribution": "oth", "yarn": {"urls": ["http://rm:8088"]}},
            }
        ),
        encoding="utf-8",
    )

    config = load_hadoop_config(creds)

    assert config.distribution == "oth"


def test_load_hadoop_config_resolves_passwords_from_the_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HADOOP_TEST_YARN_PASSWORD", "from-env")
    creds = tmp_path / ".credentials.yml"
    creds.write_text(
        yaml.safe_dump(
            {
                "secret_vault_type": "env",
                "hadoop": {
                    "distribution": "hdp",
                    "yarn": {"urls": ["http://rm:8088"], "username": "u", "password": "HADOOP_TEST_YARN_PASSWORD"},
                },
            }
        ),
        encoding="utf-8",
    )

    assert load_hadoop_config(creds).yarn.password == "from-env"  # type: ignore[union-attr]


def test_load_hadoop_config_without_a_hadoop_section_fails(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.yml"
    creds.write_text(yaml.safe_dump({"secret_vault_type": "local", "mssql": {}}), encoding="utf-8")

    with pytest.raises(HadoopConfigError, match="No 'hadoop' section"):
        load_hadoop_config(creds)


_CM = {"url": "https://cm.example.net:7183", "username": "profiler", "password": "pw"}


def test_cloudera_manager_defaults_leave_the_detection_and_impala_extract_off() -> None:
    config = parse_hadoop_config(_raw(cloudera_manager=dict(_CM)))

    cloudera = config.cloudera_manager
    assert cloudera is not None
    assert (cloudera.api_version, cloudera.cluster, cloudera.impala_service) == (None, None, None)
    assert cloudera.extract_impala_queries is False
    assert (cloudera.impala_interval_minutes, cloudera.impala_max_pages) == (60, 2)
    # Like the original CM_IMPALA_NUMBER_OF_DAYS, Impala queries look back 1 day unless given their own window.
    assert cloudera.impala_lookback_days == 1
    assert cloudera.endpoint.urls == ("https://cm.example.net:7183",)
    assert (cloudera.endpoint.username, cloudera.endpoint.password) == ("profiler", "pw")


def test_cloudera_manager_is_absent_by_default() -> None:
    assert parse_hadoop_config(_raw()).cloudera_manager is None


def test_cloudera_manager_settings_are_normalised() -> None:
    config = parse_hadoop_config(
        _raw(
            cloudera_manager={
                **_CM,
                "api_version": " V12 ",
                "cluster": "Cloudera QuickStart",
                "extract_impala_queries": "yes",
                "impala_service": "impala",
                "impala_interval_minutes": "10",
                "impala_lookback_days": 3,
                "impala_max_pages": "5",
            }
        )
    )

    cloudera = config.cloudera_manager
    assert cloudera is not None
    assert (cloudera.api_version, cloudera.cluster, cloudera.impala_service) == ("v12", "Cloudera QuickStart", "impala")
    assert (cloudera.extract_impala_queries, cloudera.impala_interval_minutes, cloudera.impala_lookback_days) == (
        True,
        10,
        3,
    )
    assert cloudera.impala_max_pages == 5


def test_cloudera_manager_url_may_carry_a_gateway_path() -> None:
    config = parse_hadoop_config(_raw(cloudera_manager={**_CM, "url": "https://gw.example.net/cm/"}))

    assert config.cloudera_manager is not None
    assert config.cloudera_manager.endpoint.urls == ("https://gw.example.net/cm",)


@pytest.mark.parametrize(
    ("cloudera", "message"),
    [
        ({"username": "u"}, "hadoop.cloudera_manager.url is required"),
        ({**_CM, "url": "cm:7180"}, "Invalid Hadoop service URL"),
        ({**_CM, "url": "https://u:p@cm:7183"}, "put credentials in username and password"),
        ({**_CM, "auth": "kerberos"}, "kerberos is not supported for cloudera_manager"),
        ({**_CM, "auth": "none"}, "auth 'none' takes no username or password"),
        ({**_CM, "api_version": "twelve"}, "api_version must look like v12"),
        ({**_CM, "impala_interval_minutes": 45}, "impala_interval_minutes must be one of 1, 2, 3, 6, 10, 20, 30, 60"),
        ({**_CM, "impala_interval_minutes": True}, "impala_interval_minutes must be one of"),
        ({**_CM, "impala_max_pages": 0}, "impala_max_pages must be a whole number from 1 to 100"),
        ({**_CM, "impala_max_pages": "many"}, "impala_max_pages must be a whole number"),
        ({**_CM, "impala_lookback_days": 0}, "hadoop.cloudera_manager.impala_lookback_days must be between 1 and 3650"),
        ({**_CM, "extract_impala_queries": "maybe"}, "extract_impala_queries must be true or false"),
        ("https://cm:7183", "hadoop.cloudera_manager must be a mapping"),
    ],
)
def test_invalid_cloudera_manager_settings_are_rejected(cloudera: object, message: str) -> None:
    with pytest.raises(HadoopConfigError, match=message):
        parse_hadoop_config(_raw(cloudera_manager=cloudera))


# ---- Apache Ranger ----------------------------------------------------------------------------------------------------


def test_ranger_is_absent_by_default() -> None:
    assert parse_hadoop_config(_raw()).ranger is None


def test_ranger_block_is_parsed_with_basic_auth_and_tls_settings() -> None:
    config = parse_hadoop_config(
        _raw(
            ranger={
                "url": "https://ranger.example.com:6182/",
                "username": "admin",
                "password": "pw",
                "verify_ssl": False,
            }
        )
    )

    assert config.ranger is not None
    assert config.ranger.urls == ("https://ranger.example.com:6182",)
    assert (config.ranger.username, config.ranger.password, config.ranger.verify) == ("admin", "pw", False)


def test_ranger_url_may_carry_a_gateway_path() -> None:
    config = parse_hadoop_config(_raw(ranger={"url": "https://c.azurehdinsight.net/ranger", "auth": "none"}))

    assert config.ranger is not None
    assert config.ranger.urls == ("https://c.azurehdinsight.net/ranger",)
    assert config.ranger.username is None


@pytest.mark.parametrize(
    ("ranger", "message"),
    [
        ({"auth": "none"}, "hadoop.ranger.url is required"),
        ({"url": "ftp://ranger:6182"}, "Invalid Hadoop service URL"),
        ({"url": "https://ranger:6182", "auth": "kerberos"}, "kerberos is not supported for ranger"),
        ({"url": "https://ranger:6182", "auth": "basic"}, "auth 'basic' needs a username"),
        ({"url": "https://ranger:6182", "auth": "none", "username": "admin"}, "auth 'none' takes no username"),
        ({"url": "https://u:p@ranger:6182"}, "not in the URL"),
        ({"url": "https://ranger:6182", "verify_ssl": "/no/such/ca.pem"}, "CA bundle not found"),
    ],
)
def test_invalid_ranger_settings_are_rejected(ranger: dict, message: str) -> None:
    with pytest.raises(HadoopConfigError, match=message):
        parse_hadoop_config(_raw(ranger=ranger))


# ---- Kerberos (SPNEGO): yarn and spark_history only ---------------------------------------------------------------------


def test_kerberos_yarn_block_is_parsed_with_its_ticket_settings(tmp_path: Path) -> None:
    keytab = tmp_path / "profiler.keytab"
    keytab.write_text("synthetic", encoding="utf-8")
    krb5 = tmp_path / "krb5.conf"
    krb5.write_text("synthetic", encoding="utf-8")
    raw = _raw(
        yarn={
            "urls": ["http://rm1:8088"],
            "auth": "kerberos",
            "principal": "profiler@HADOOP.SIM",
            "keytab": str(keytab),
            "krb5_config": str(krb5),
            "ccache": "FILE:/tmp/krb-profiler.cc",
        }
    )

    yarn = parse_hadoop_config(raw).yarn

    assert yarn is not None
    assert yarn.username is None and yarn.password is None
    assert yarn.kerberos == KerberosSettings(
        principal="profiler@HADOOP.SIM",
        keytab=str(keytab),
        ccache="FILE:/tmp/krb-profiler.cc",
        krb5_config=str(krb5),
    )


def test_kerberos_spark_history_may_use_an_existing_ticket() -> None:
    spark = parse_hadoop_config(_raw(spark_history={"url": "http://shs:18080", "auth": "kerberos"})).spark_history

    assert spark is not None
    assert spark.kerberos == KerberosSettings()
    assert spark.urls == ("http://shs:18080",)


def test_non_kerberos_endpoints_have_no_kerberos_settings() -> None:
    assert parse_hadoop_config(_raw()).yarn.kerberos is None  # type: ignore[union-attr]


def test_keytab_path_is_expanded_and_must_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keytab = tmp_path / "profiler.keytab"
    keytab.write_text("synthetic", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))

    spark = parse_hadoop_config(
        _raw(
            spark_history={
                "url": "http://shs:18080",
                "auth": "kerberos",
                "principal": "p@R",
                "keytab": "~/profiler.keytab",
            }
        )
    ).spark_history

    assert spark is not None and spark.kerberos is not None
    assert spark.kerberos.keytab == str(keytab)


@pytest.mark.parametrize(
    ("yarn", "message"),
    [
        ({"urls": ["http://rm1:8088"], "auth": "kerberos", "username": "u"}, "takes no username or password"),
        ({"urls": ["http://rm1:8088"], "auth": "kerberos", "password": "p"}, "takes no username or password"),
        (
            {"urls": ["http://rm1:8088"], "auth": "kerberos", "keytab": "/no/such/profiler.keytab", "principal": "p@R"},
            r"hadoop\.yarn\.keytab not found at '/no/such/profiler.keytab'",
        ),
        ({"urls": ["http://rm1:8088"], "auth": "kerberos", "keytab": "/etc/hosts"}, "a keytab needs a principal"),
        (
            {"urls": ["http://rm1:8088"], "auth": "kerberos", "krb5_config": "/no/such/krb5.conf"},
            r"hadoop\.yarn\.krb5_config not found",
        ),
    ],
)
def test_invalid_kerberos_yarn_settings_are_rejected(yarn: dict, message: str) -> None:
    with pytest.raises(HadoopConfigError, match=message):
        parse_hadoop_config(_raw(yarn=yarn))


@pytest.mark.parametrize("section", ["cloudera_manager", "ranger", "impala"])
def test_kerberos_is_rejected_outside_yarn_and_spark_history(section: str) -> None:
    block: dict[str, Any] = {"url": "https://cm:7183"} if section != "impala" else {"urls": ["http://impalad:25000"]}
    block["auth"] = "kerberos"
    raw = _raw(**{section: block})

    with pytest.raises(HadoopConfigError, match=f"kerberos is not supported for {section}"):
        parse_hadoop_config(raw)
