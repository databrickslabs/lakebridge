"""Parse and validate the ``hadoop`` section of the profiler credentials file.

Example::

    secret_vault_type: local
    hadoop:
      distribution: cdh              # cdh | hdp | oth, as the original profiler's DISTRIBUTION
      yarn:
        urls:                        # HA: list every ResourceManager; a gateway URL may carry a path
          - http://rm1.example.com:8088
          - http://rm2.example.com:8088
        auth: basic                  # none | basic | kerberos (kerberos: yarn and spark_history only)
        username: profiler
        password: ...                # literal, or an environment variable name when secret_vault_type is env
        verify_ssl: true             # true | false | path to a CA bundle
        app_filter: "user=etl"       # optional YARN apps query filter (the original RM_APP_FILTER)
      spark_history:                 # optional: the Spark steps are skipped when this is absent
        url: https://spark-hs.example.com:18081
        auth: none                   # none | basic | kerberos
        verify_ssl: true
        # kerberos settings (yarn and spark_history only), with auth: kerberos:
        #   principal: profiler@EXAMPLE.COM   # required with keytab; optional otherwise
        #   keytab: /etc/security/keytabs/profiler.keytab   # optional; kinit -kt runs unless a valid ticket is cached
        #   ccache: FILE:/tmp/krb5cc_profiler  # optional (KRB5CCNAME); a keytab without it uses the profiler's own cache
        #   krb5_config: /etc/krb5.conf         # optional; the Kerberos config (KRB5_CONFIG)
        # without a keytab, an existing ticket (kinit beforehand) is used and checked with klist.
      cloudera_manager:              # optional: the CM steps are skipped when this is absent (CDH and CDP)
        url: https://cm.example.com:7183
        auth: basic
        username: profiler
        password: ...
        verify_ssl: true
        api_version: v12             # optional; detected from /api/version when absent
        cluster: "Cloudera QuickStart"   # optional; detected when CM manages exactly one cluster
        extract_impala_queries: false    # opt-in: the Impala query extract is slow on busy clusters
        impala_service: impala       # optional; detected when the cluster has exactly one IMPALA service
        impala_interval_minutes: 60  # 1, 2, 3, 6, 10, 20, 30 or 60
        impala_lookback_days: 1      # optional; 1 day, like the original CM_IMPALA_NUMBER_OF_DAYS
        impala_max_pages: 2          # pages of 1000 queries per time interval
      ranger:                        # optional: the Ranger steps are skipped when this is absent
        url: https://ranger.example.com:6182   # Ranger Admin; may include a path (an HDInsight gateway)
        auth: basic
        username: profiler
        password: ...
        verify_ssl: true
      impala:                        # optional: direct read of each Impala daemon's web UI (no Cloudera Manager)
        urls:                        # every coordinator impalad; each one knows only the queries it coordinated
          - http://impalad1.example.com:25000
          - http://impalad2.example.com:25000
        auth: none                   # none | basic; the same credentials are used for every URL below
        catalogd_url: http://catalogd.example.com:25020     # optional; enables the impala_catalog step
        statestored_url: http://statestored.example.com:25010   # optional; enables the impala_statestore step
        verify_ssl: true
      ambari:                        # optional: the Ambari steps are skipped when this is absent (HDP and HDInsight)
        url: http://ambari.example.com:8080   # or the HDInsight gateway: https://<cluster>.azurehdinsight.net (no port)
        auth: basic
        username: admin
        password: ...
        verify_ssl: true
        cluster: "Sandbox HDP"       # optional; detected when Ambari manages exactly one cluster
      search_replace_file: /etc/lakebridge/masking.txt   # optional masking rules file (s/find/replace/g lines), read every run

Other sections are ignored here.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.resources.assessments.hadoop.kerberos import KerberosRestClient, KerberosSettings
from databricks.labs.lakebridge.resources.assessments.hadoop.masking import (
    MaskingRulesError,
    SearchReplaceRule,
    apply_masking_rules,
    load_masking_rules,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import RestClient, normalize_base_url

# The original profiler's DISTRIBUTION values: oth (Apache Hadoop and other distributions), cdh (CDH, CDP Private
# Cloud Base) and hdp (HDP, Azure HDInsight).
DISTRIBUTIONS = ("oth", "cdh", "hdp")
SUPPORTED_AUTH = ("none", "basic")
# Kerberos (SPNEGO) only where the original supports it: the ResourceManager and the Spark History Server.
KERBEROS_SECTIONS = ("yarn", "spark_history")
# As the original's CM_IMPALA_NUMBER_OF_DAYS=1: yesterday and today.
DEFAULT_IMPALA_LOOKBACK_DAYS = 1
MAX_IMPALA_LOOKBACK_DAYS = 3650
# Divisors of an hour, as the original's CM_IMPALA_INTERVAL_MINUTES.
IMPALA_INTERVAL_OPTIONS = (1, 2, 3, 6, 10, 20, 30, 60)
DEFAULT_IMPALA_INTERVAL_MINUTES = 60
DEFAULT_IMPALA_MAX_PAGES = 2
MAX_IMPALA_PAGES = 100
_API_VERSION_PATTERN = re.compile(r"^v\d+$")


class HadoopConfigError(ValueError):
    """The ``hadoop`` credentials section is missing, incomplete or invalid."""


@dataclass(frozen=True)
class RestEndpoint:
    """Connection settings for one REST service: a ResourceManager set or a Spark History Server."""

    urls: tuple[str, ...]
    username: str | None = None
    password: str | None = None
    verify: bool | str = True
    # Extra query parameters for the YARN applications request (the original profiler's RM_APP_FILTER).
    app_filter: tuple[tuple[str, str], ...] = ()
    # Set with auth: kerberos; requests then go through curl with SPNEGO.
    kerberos: KerberosSettings | None = None

    def client(self, url: str) -> RestClient:
        """The client for one of this endpoint's URLs, with the endpoint's authentication."""
        if self.kerberos is not None:
            return KerberosRestClient(url, kerberos=self.kerberos, verify=self.verify)
        return RestClient(url, username=self.username, password=self.password, verify=self.verify)


@dataclass(frozen=True)
class ClouderaManagerConfig:
    """Connection and extract settings for Cloudera Manager (CDH and CDP)."""

    endpoint: RestEndpoint
    # Detected from /api/version when None, for example "v12".
    api_version: str | None = None
    # None: detected when CM manages exactly one cluster. Names may contain spaces.
    cluster: str | None = None
    extract_impala_queries: bool = False
    impala_service: str | None = None
    impala_interval_minutes: int = DEFAULT_IMPALA_INTERVAL_MINUTES
    impala_lookback_days: int = DEFAULT_IMPALA_LOOKBACK_DAYS
    impala_max_pages: int = DEFAULT_IMPALA_MAX_PAGES


@dataclass(frozen=True)
class AmbariConfig:
    """The Ambari Server, or an HDInsight gateway, that serves the cluster's blueprint, hosts and components."""

    endpoint: RestEndpoint
    # None: detected when Ambari manages exactly one cluster. Names may contain spaces.
    cluster: str | None = None


@dataclass(frozen=True)
class ImpalaConfig:
    """Impala debug web UIs: one impalad per coordinator, plus optional catalogd and statestored."""

    # The impalad web UIs (port 25000 by default), read one by one and unioned.
    endpoint: RestEndpoint
    catalogd_url: str | None = None
    statestored_url: str | None = None


@dataclass(frozen=True)
class HadoopConfig:
    distribution: str
    yarn: RestEndpoint | None
    spark_history: RestEndpoint | None
    # The rules from hadoop.search_replace_file, in file order; empty without a file.
    search_replace: tuple[SearchReplaceRule, ...] = ()
    cloudera_manager: ClouderaManagerConfig | None = None
    ranger: RestEndpoint | None = None
    impala: ImpalaConfig | None = None
    ambari: AmbariConfig | None = None

    def mask(self, text: str) -> str:
        """Apply the masking rules to one raw REST response."""
        return apply_masking_rules(self.search_replace, text)


def parse_hadoop_config(raw: Any) -> HadoopConfig:
    """Validate the resolved ``hadoop`` section and return it as a ``HadoopConfig``."""
    if not isinstance(raw, Mapping):
        raise HadoopConfigError("The hadoop credentials must be a mapping")
    distribution = str(raw.get("distribution") or "").strip().lower()
    if distribution not in DISTRIBUTIONS:
        raise HadoopConfigError(
            f"hadoop.distribution must be one of {', '.join(DISTRIBUTIONS)} "
            "(Azure HDInsight is hdp; Apache Hadoop and other distributions are oth); "
            f"got {raw.get('distribution')!r}"
        )
    return HadoopConfig(
        distribution=distribution,
        yarn=_endpoint(raw.get("yarn"), "yarn", multiple_urls=True),
        spark_history=_endpoint(raw.get("spark_history"), "spark_history", multiple_urls=False),
        search_replace=_search_replace_rules(raw.get("search_replace_file")),
        cloudera_manager=_cloudera_manager(raw.get("cloudera_manager")),
        ranger=_endpoint(raw.get("ranger"), "ranger", multiple_urls=False),
        impala=_impala(raw.get("impala")),
        ambari=_ambari(raw.get("ambari")),
    )


def load_hadoop_config(creds_file: Path) -> HadoopConfig:
    """Load the ``hadoop`` section through the credential manager, so secrets resolve as for other sources."""
    manager = create_credential_manager(PRODUCT_NAME, EnvGetter(), creds_path=creds_file)
    try:
        raw = manager.get_credentials("hadoop")
    except KeyError as e:
        raise HadoopConfigError("No 'hadoop' section in the credentials file") from e
    return parse_hadoop_config(raw)


def _whole_days(raw: Any, key: str) -> int:
    if isinstance(raw, bool):
        raise HadoopConfigError(f"{key} must be a whole number of days")
    try:
        value = int(raw)
    except (TypeError, ValueError) as e:
        raise HadoopConfigError(f"{key} must be a whole number of days") from e
    if not 1 <= value <= MAX_IMPALA_LOOKBACK_DAYS:
        raise HadoopConfigError(f"{key} must be between 1 and {MAX_IMPALA_LOOKBACK_DAYS}")
    return value


def _cloudera_manager(raw: Any) -> ClouderaManagerConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise HadoopConfigError("hadoop.cloudera_manager must be a mapping")
    endpoint = _endpoint(raw, "cloudera_manager", multiple_urls=False)
    if endpoint is None:  # pragma: no cover  (raw is a mapping, so _endpoint never returns None here)
        raise HadoopConfigError("hadoop.cloudera_manager is not configured")

    api_version = _optional_text(raw.get("api_version"))
    if api_version is not None:
        api_version = api_version.strip().lower()
        if not _API_VERSION_PATTERN.match(api_version):
            raise HadoopConfigError(f"hadoop.cloudera_manager.api_version must look like v12; got {api_version!r}")

    interval = raw.get("impala_interval_minutes", DEFAULT_IMPALA_INTERVAL_MINUTES)
    if isinstance(interval, bool) or str(interval).strip() not in (str(option) for option in IMPALA_INTERVAL_OPTIONS):
        raise HadoopConfigError(
            "hadoop.cloudera_manager.impala_interval_minutes must be one of "
            f"{', '.join(str(option) for option in IMPALA_INTERVAL_OPTIONS)}; got {interval!r}"
        )

    max_pages = raw.get("impala_max_pages", DEFAULT_IMPALA_MAX_PAGES)
    if (
        isinstance(max_pages, bool)
        or not str(max_pages).strip().isdigit()
        or not 1 <= int(max_pages) <= MAX_IMPALA_PAGES
    ):
        raise HadoopConfigError(
            f"hadoop.cloudera_manager.impala_max_pages must be a whole number from 1 to {MAX_IMPALA_PAGES}"
        )

    impala_lookback = raw.get("impala_lookback_days")
    return ClouderaManagerConfig(
        endpoint=endpoint,
        api_version=api_version,
        cluster=_optional_text(raw.get("cluster")),
        extract_impala_queries=_flag(
            raw.get("extract_impala_queries", False), "hadoop.cloudera_manager.extract_impala_queries"
        ),
        impala_service=_optional_text(raw.get("impala_service")),
        impala_interval_minutes=int(interval),
        impala_lookback_days=(
            DEFAULT_IMPALA_LOOKBACK_DAYS
            if impala_lookback is None
            else _whole_days(impala_lookback, "hadoop.cloudera_manager.impala_lookback_days")
        ),
        impala_max_pages=int(max_pages),
    )


def _impala(raw: Any) -> ImpalaConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise HadoopConfigError("hadoop.impala must be a mapping")
    endpoint = _endpoint(raw, "impala", multiple_urls=True)
    if endpoint is None:  # pragma: no cover  (raw is a mapping, so _endpoint never returns None here)
        raise HadoopConfigError("hadoop.impala is not configured")
    return ImpalaConfig(
        endpoint=endpoint,
        catalogd_url=_optional_service_url(raw.get("catalogd_url"), "hadoop.impala.catalogd_url"),
        statestored_url=_optional_service_url(raw.get("statestored_url"), "hadoop.impala.statestored_url"),
    )


def _ambari(raw: Any) -> AmbariConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise HadoopConfigError("hadoop.ambari must be a mapping")
    endpoint = _endpoint(raw, "ambari", multiple_urls=False)
    if endpoint is None:  # pragma: no cover  (raw is a mapping, so _endpoint never returns None here)
        raise HadoopConfigError("hadoop.ambari is not configured")
    cluster = _optional_text(raw.get("cluster"))
    return AmbariConfig(endpoint=endpoint, cluster=(cluster.strip() if cluster else None) or None)


def _optional_service_url(raw: Any, key: str) -> str | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise HadoopConfigError(f"{key} must be a URL")
    try:
        return normalize_base_url(raw)
    except ValueError as e:
        raise HadoopConfigError(f"{key}: {e}") from e


def _flag(raw: Any, key: str) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("true", "yes", "y"):
        return True
    if text in ("false", "no", "n", ""):
        return False
    raise HadoopConfigError(f"{key} must be true or false; got {raw!r}")


def _endpoint(raw: Any, section: str, *, multiple_urls: bool) -> RestEndpoint | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise HadoopConfigError(f"hadoop.{section} must be a mapping")

    auth = str(raw.get("auth") or "").strip().lower()
    username = _optional_text(raw.get("username"))
    password = _optional_text(raw.get("password"))
    if not auth:
        auth = "basic" if username else "none"
    if auth == "kerberos":
        if section not in KERBEROS_SECTIONS:
            raise HadoopConfigError(
                f"hadoop.{section}.auth: kerberos is not supported for {section}; Kerberos is supported only for "
                "yarn and spark_history, like the original profiler; use none or basic"
            )
        return _kerberos_endpoint(raw, section, multiple_urls=multiple_urls)
    if auth not in SUPPORTED_AUTH:
        raise HadoopConfigError(f"hadoop.{section}.auth must be one of {', '.join(SUPPORTED_AUTH)}; got {auth!r}")
    if auth == "basic" and not username:
        raise HadoopConfigError(f"hadoop.{section}: auth 'basic' needs a username")
    if auth == "none" and (username or password):
        raise HadoopConfigError(f"hadoop.{section}: auth 'none' takes no username or password")

    return RestEndpoint(
        urls=_urls(raw, section, multiple_urls=multiple_urls),
        username=username if auth == "basic" else None,
        password=password if auth == "basic" else None,
        verify=_verify(raw.get("verify_ssl", True), section),
        app_filter=parse_app_filter(raw.get("app_filter"), section) if multiple_urls else (),
    )


def _kerberos_endpoint(raw: Mapping[str, Any], section: str, *, multiple_urls: bool) -> RestEndpoint:
    if _optional_text(raw.get("username")) or _optional_text(raw.get("password")):
        raise HadoopConfigError(
            f"hadoop.{section}: auth 'kerberos' takes no username or password; the ticket identifies the profiler"
        )
    principal = _optional_text(raw.get("principal"))
    keytab = _optional_path(raw.get("keytab"), f"hadoop.{section}.keytab")
    krb5_config = _optional_path(raw.get("krb5_config"), f"hadoop.{section}.krb5_config")
    if keytab is not None and principal is None:
        raise HadoopConfigError(f"hadoop.{section}: a keytab needs a principal (for example profiler@EXAMPLE.COM)")
    return RestEndpoint(
        urls=_urls(raw, section, multiple_urls=multiple_urls),
        verify=_verify(raw.get("verify_ssl", True), section),
        app_filter=parse_app_filter(raw.get("app_filter"), section) if multiple_urls else (),
        kerberos=KerberosSettings(
            principal=principal.strip() if principal else None,
            keytab=keytab,
            ccache=_optional_text(raw.get("ccache")),
            krb5_config=krb5_config,
        ),
    )


def _optional_path(raw: Any, key: str) -> str | None:
    """A file the profiler reads (a keytab or a krb5.conf): it must exist. A leading ~ is expanded."""
    text = _optional_text(raw)
    if text is None or not text.strip():
        return None
    path = Path(text.strip()).expanduser()
    if not path.is_file():
        raise HadoopConfigError(f"{key} not found at {text.strip()!r}")
    return str(path)


def parse_app_filter(raw: Any, section: str = "yarn") -> tuple[tuple[str, str], ...]:
    """Parse a YARN query string such as ``user=etl&queue=root.prod`` (a leading ``?`` is allowed)."""
    if raw is None or raw == "":
        return ()
    if not isinstance(raw, str):
        raise HadoopConfigError(f"hadoop.{section}.app_filter must be a query string such as 'user=etl'")
    try:
        pairs = parse_qsl(raw.strip().lstrip("?"), keep_blank_values=False, strict_parsing=True)
    except ValueError as e:
        raise HadoopConfigError(f"hadoop.{section}.app_filter is not a valid query string: {raw!r}") from e
    return tuple(pairs)


def _search_replace_rules(raw: Any) -> tuple[SearchReplaceRule, ...]:
    """The masking rules file, read on every load. Blank means no masking, as in the original."""
    if raw is None:
        return ()
    if not isinstance(raw, str):
        raise HadoopConfigError("hadoop.search_replace_file must be the path to a masking rules file")
    if not raw.strip():
        return ()
    try:
        return load_masking_rules(raw.strip())
    except MaskingRulesError as e:
        raise HadoopConfigError(f"hadoop.search_replace_file: {e}") from e


def _urls(raw: Mapping[str, Any], section: str, *, multiple_urls: bool) -> tuple[str, ...]:
    if multiple_urls:
        candidates = raw.get("urls")
        if isinstance(candidates, str):
            candidates = candidates.split(",")
        if not isinstance(candidates, list) or not any(str(url).strip() for url in candidates):
            raise HadoopConfigError(f"hadoop.{section}.urls must be a non-empty list of URLs")
    else:
        single = raw.get("url")
        if not isinstance(single, str) or not single.strip():
            raise HadoopConfigError(f"hadoop.{section}.url is required")
        candidates = [single]
    try:
        return tuple(normalize_base_url(str(url)) for url in candidates if str(url).strip())
    except ValueError as e:
        raise HadoopConfigError(f"hadoop.{section}: {e}") from e


def _verify(raw: Any, section: str) -> bool | str:
    if raw is None or raw is True:
        return True
    if raw is False:
        return False
    if isinstance(raw, str):
        text = raw.strip()
        if text.lower() in ("true", "yes"):
            return True
        if text.lower() in ("false", "no"):
            return False
        if text and Path(text).is_file():
            return text
        if text:
            raise HadoopConfigError(f"hadoop.{section}.verify_ssl: CA bundle not found at {text!r}")
    raise HadoopConfigError(f"hadoop.{section}.verify_ssl must be true, false or a path to a CA bundle")


def _optional_text(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw)
    return text if text else None
