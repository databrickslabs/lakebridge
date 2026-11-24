"""Parse and validate the ``hadoop`` section of the profiler credentials file.

Example::

    secret_vault_type: local
    hadoop:
      distribution: cdh              # oth | mapr | cdh | hdp | hdi
      lookback_days: 30
      yarn:
        urls:                        # HA: list every ResourceManager; a gateway URL may carry a path
          - http://rm1.example.com:8088
          - http://rm2.example.com:8088
        auth: basic                  # none | basic (kerberos is not supported yet)
        username: profiler
        password: ...                # literal, or an environment variable name when secret_vault_type is env
        verify_ssl: true             # true | false | path to a CA bundle
        app_filter: "user=etl"       # optional YARN apps query filter (the original RM_APP_FILTER)
      spark_history:                 # optional: the Spark steps are skipped when this is absent
        url: https://spark-hs.example.com:18081
        auth: none
        verify_ssl: true
      mask_rules:                    # optional search/replace on every REST response (the original sed.txt)
        - pattern: 'internal\.example\.com'
          replacement: 'masked.example'

Other sections (for example ``cloudera_manager``, ``ambari`` or ``ranger``) are added in later phases and are
ignored here.
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
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import normalize_base_url

DISTRIBUTIONS = ("oth", "mapr", "cdh", "hdp", "hdi")
SUPPORTED_AUTH = ("none", "basic")
DEFAULT_LOOKBACK_DAYS = 30
MAX_LOOKBACK_DAYS = 3650


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


@dataclass(frozen=True)
class MaskRule:
    """A search/replace applied to every REST response before it is parsed (the original profiler's sed.txt)."""

    pattern: re.Pattern[str]
    replacement: str

    def apply(self, text: str) -> str:
        return self.pattern.sub(self.replacement, text)


@dataclass(frozen=True)
class HadoopConfig:
    distribution: str
    lookback_days: int
    yarn: RestEndpoint | None
    spark_history: RestEndpoint | None
    mask_rules: tuple[MaskRule, ...] = ()

    def mask(self, text: str) -> str:
        for rule in self.mask_rules:
            text = rule.apply(text)
        return text


def parse_hadoop_config(raw: Any) -> HadoopConfig:
    """Validate the resolved ``hadoop`` section and return it as a ``HadoopConfig``."""
    if not isinstance(raw, Mapping):
        raise HadoopConfigError("The hadoop credentials must be a mapping")
    distribution = str(raw.get("distribution") or "").strip().lower()
    if distribution not in DISTRIBUTIONS:
        raise HadoopConfigError(
            f"hadoop.distribution must be one of {', '.join(DISTRIBUTIONS)}; got {raw.get('distribution')!r}"
        )
    return HadoopConfig(
        distribution=distribution,
        lookback_days=_lookback_days(raw.get("lookback_days", DEFAULT_LOOKBACK_DAYS)),
        yarn=_endpoint(raw.get("yarn"), "yarn", multiple_urls=True),
        spark_history=_endpoint(raw.get("spark_history"), "spark_history", multiple_urls=False),
        mask_rules=_mask_rules(raw.get("mask_rules")),
    )


def load_hadoop_config(creds_file: Path) -> HadoopConfig:
    """Load the ``hadoop`` section through the credential manager, so secrets resolve like other sources."""
    manager = create_credential_manager(PRODUCT_NAME, EnvGetter(), creds_path=creds_file)
    try:
        raw = manager.get_credentials("hadoop")
    except KeyError as e:
        raise HadoopConfigError("No 'hadoop' section in the credentials file") from e
    return parse_hadoop_config(raw)


def _lookback_days(raw: Any) -> int:
    if isinstance(raw, bool):
        raise HadoopConfigError("hadoop.lookback_days must be a whole number of days")
    try:
        value = int(raw)
    except (TypeError, ValueError) as e:
        raise HadoopConfigError("hadoop.lookback_days must be a whole number of days") from e
    if not 1 <= value <= MAX_LOOKBACK_DAYS:
        raise HadoopConfigError(f"hadoop.lookback_days must be between 1 and {MAX_LOOKBACK_DAYS}")
    return value


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
        raise HadoopConfigError(f"hadoop.{section}.auth: kerberos is not supported yet; use none or basic")
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
        app_filter=_app_filter(raw.get("app_filter"), section) if multiple_urls else (),
    )


def _app_filter(raw: Any, section: str) -> tuple[tuple[str, str], ...]:
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


def _mask_rules(raw: Any) -> tuple[MaskRule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise HadoopConfigError("hadoop.mask_rules must be a list of {pattern, replacement} entries")
    rules = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping) or not isinstance(entry.get("pattern"), str) or not entry["pattern"]:
            raise HadoopConfigError(f"hadoop.mask_rules[{index}] needs a non-empty 'pattern'")
        replacement = entry.get("replacement", "")
        if not isinstance(replacement, str):
            raise HadoopConfigError(f"hadoop.mask_rules[{index}].replacement must be text")
        try:
            pattern = re.compile(entry["pattern"])
        except re.error as e:
            raise HadoopConfigError(f"hadoop.mask_rules[{index}].pattern is not a valid regex: {e}") from e
        rules.append(MaskRule(pattern=pattern, replacement=replacement))
    return tuple(rules)


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
