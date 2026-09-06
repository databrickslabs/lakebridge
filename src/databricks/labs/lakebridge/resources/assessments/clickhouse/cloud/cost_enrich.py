"""Optional ClickHouse Cloud cost enrichment step.

The one Python step in the ClickHouse profiler. Everything else is SQL/DDL; this exists only because
the authoritative billing figures live behind the ClickHouse Cloud REST API (HTTP Basic auth to
``api.clickhouse.cloud``), which SQL cannot reach. It fetches provider/region, the real compute
sizing, the plan tier, and the actual billed cost for the 30-day window, then writes a single row to
``costs_pricing_config`` (the same typed table the ``*_ddl.sql`` created).

Declared ``optional: true`` in ``cloud/pipeline_config.yml``: with no ``cloud_api`` credentials, or if
the API is unreachable, the step exits successfully having written a "no enrichment" row, so a missing
billing API degrades gracefully instead of failing the run. The ``system.*`` cost attribution
(storage/compute/scan/ingestion buckets, resource footprint) is produced entirely by the SQL steps and
does not depend on this step.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from databricks.labs.blueprint.entrypoint import get_logger

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.connections.credential_manager import CredentialManager, create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.resources.assessments.clickhouse import is_cloud_host
from databricks.labs.lakebridge.resources.assessments.clickhouse.cloud_api import ClickHouseCloudAPI, CloudAPIError
from databricks.labs.lakebridge.resources.assessments.common.cli import arguments_loader
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb

logger = get_logger(__file__)

TABLE_NAME = "costs_pricing_config"
# Must match objects_.../costs_pricing_config_ddl.sql exactly (save_to_duckdb recreates the table
# from this schema before insert).
TABLE_SCHEMA = (
    "region_detected VARCHAR, region_source VARCHAR, tier VARCHAR, tier_source VARCHAR, "
    "is_cloud BOOLEAN, note VARCHAR, cloud_service VARCHAR, actual_billed_cost VARCHAR"
)

_CLOUD_PROVIDERS = ("aws", "gcp", "azure")
_NO_CREDS_NOTE = (
    "ClickHouse Cloud detected but no cloud_api credentials were configured, so no billed-cost "
    "enrichment was performed. The resource footprint and all usage attribution (from the SQL steps) "
    "are still reported. Set cloud_api.key_id / cloud_api.key_secret to enrich with actual billed cost."
)
_API_FAIL_NOTE = (
    "ClickHouse Cloud detected but the Cloud API could not be reached, so no billed-cost enrichment "
    "was performed. See warnings for the cause. Usage attribution from the SQL steps is unaffected."
)
_ENRICHED_NOTE = (
    "Dollar figures are the actual billed cost from the ClickHouse Cloud usageCost API. No rate-card "
    "estimation is performed. Provider/region/tier are recorded as metadata."
)
_ENRICHED_NO_COST_NOTE = (
    "ClickHouse Cloud service metadata (provider/region/tier/sizing) was captured, but the usageCost "
    "API returned no billed cost for this window (see warnings). No dollar figures are reported; usage "
    "attribution from the SQL steps is unaffected."
)


def _has_billed_cost(cloud_meta: dict | None) -> bool:
    """True when the usageCost summary has billable data: per-service records or an org grand total.

    The org total (grandTotalCHC) can be present with zero service records (a new service in a billing
    org), so gating on ``record_count`` alone would drop the org-wide TCO.
    """
    actual = (cloud_meta or {}).get("actual_cost") or {}
    return bool(actual.get("record_count")) or actual.get("org_total_usd") is not None


def _detect_region(config: dict[str, Any], cloud_meta: dict | None) -> tuple[str, str]:
    """Return ``(region, source)`` from config override, Cloud API metadata, or the hostname."""
    if config.get("region"):
        return str(config["region"]), "config"
    if cloud_meta and cloud_meta.get("region_key") not in (None, "default"):
        return cloud_meta["region_key"], "cloud_api"
    host = str(config.get("host", ""))
    if is_cloud_host(host):
        prefix = host.strip().lower().split(".clickhouse.cloud", 1)[0]
        tokens = prefix.split(".")
        if len(tokens) >= 3 and tokens[-1] in _CLOUD_PROVIDERS:
            return f"{tokens[-1]}:{tokens[-2]}", "hostname"
        if len(tokens) >= 2:
            # Legacy host without a provider token: the region is knowable but the provider is not
            # (ClickHouse Cloud runs on AWS, GCP, and Azure), so report the region alone rather than
            # guessing a provider. When Cloud API metadata is available it supplies the authoritative
            # provider via the branch above.
            return tokens[-1], "hostname"
    return "default", "hostname"


def _config_tier_override(config: dict[str, Any]) -> str | None:
    """Explicit plan-tier override: top-level ``tier`` wins over ``cloud_api.tier``; blank is unset."""
    chosen = config.get("tier") or (config.get("cloud_api") or {}).get("tier")
    chosen = chosen.strip() if isinstance(chosen, str) else chosen
    return chosen or None


def _fetch_cloud_metadata(config: dict[str, Any], warnings: list[str], api: ClickHouseCloudAPI) -> dict | None:
    """Fetch service metadata + usageCost summary from the Cloud API, or None on error."""
    api_cfg = config.get("cloud_api") or {}
    meta = api.discover_service(
        org_id=api_cfg.get("organization_id"),
        service_id=api_cfg.get("service_id"),
        host=config.get("host"),
    )
    # usageCost: authoritative plan tier (organizationTier) + actual billed cost. The API allows at
    # most a 31-day span; `today - 29 days .. today` is 30 inclusive calendar dates, which stays
    # safely under that limit while covering ~30 days (matching the SQL steps' INTERVAL 30 DAY).
    try:
        today = datetime.now(timezone.utc).date()
        usage = api.get_usage_cost(meta["organization_id"], (today - timedelta(days=29)).isoformat(), today.isoformat())
        summary = api.summarize_usage_cost(usage, service_id=meta.get("service_id"))
        if summary.get("tier"):
            meta["tier"] = summary["tier"]
            meta["tier_source"] = "usage_cost"
            meta["tier_as_of_date"] = summary.get("tier_as_of_date")
        meta["actual_cost"] = summary
        meta["actual_cost_window_days"] = 30
    except (CloudAPIError, KeyError, ValueError) as e:
        warnings.append(f"cloud_api usageCost: {str(e)[:200]}")
    return meta


def build_pricing_row(config: dict[str, Any], cloud_meta: dict | None, note: str) -> dict[str, Any]:
    """Assemble the single costs_pricing_config row. Nested objects are JSON-encoded into VARCHARs.

    Public (not underscore-prefixed) because it is the pure, side-effect-free core of this step —
    tier precedence and billed-cost projection — and is exercised directly by unit tests.
    """
    region, region_source = _detect_region(config, cloud_meta)
    config_tier = _config_tier_override(config)
    tier = config_tier or (cloud_meta or {}).get("tier")
    tier_source = "config" if config_tier else ((cloud_meta or {}).get("tier_source") or "unknown")

    cloud_service: dict[str, Any] | None = None
    actual_billed_cost: dict[str, Any] | None = None
    if cloud_meta:
        cloud_service = {
            "service_id": cloud_meta.get("service_id"),
            "name": cloud_meta.get("name"),
            "provider": cloud_meta.get("provider"),
            "region": cloud_meta.get("region"),
            "state": cloud_meta.get("state"),
            "profile": cloud_meta.get("profile"),
            "min_replica_memory_gb": cloud_meta.get("min_replica_memory_gb"),
            "num_replicas": cloud_meta.get("num_replicas"),
            "tier": cloud_meta.get("tier"),
            "tier_source": cloud_meta.get("tier_source"),
            "tier_as_of_date": cloud_meta.get("tier_as_of_date"),
        }
        actual = cloud_meta.get("actual_cost") or {}
        if _has_billed_cost(cloud_meta):
            window_days = cloud_meta.get("actual_cost_window_days") or 30
            service_total = actual.get("actual_total_usd")
            org_total = actual.get("org_total_usd")
            tco_total = org_total if org_total is not None else service_total
            actual_billed_cost = {
                "source": "cloud_usage_cost_api",
                "window_days": window_days,
                "currency": "CHC (1 CHC ~= 1 USD, before discounts)",
                "breakdown_chc": actual.get("actual_cost_chc"),
                "service_total_usd": service_total,
                "org_total_usd": org_total,
                "total_usd": tco_total,
                "monthly_total_usd_projected": (
                    round(tco_total * (30 / max(window_days, 1)), 2) if tco_total else None
                ),
            }

    return {
        "region_detected": region,
        "region_source": region_source,
        "tier": tier,
        "tier_source": tier_source,
        "is_cloud": True,
        "note": note,
        "cloud_service": json.dumps(cloud_service) if cloud_service else None,
        "actual_billed_cost": json.dumps(actual_billed_cost) if actual_billed_cost else None,
    }


def execute(
    credential_manager: CredentialManager, db_path: str, client: ClickHouseCloudAPI | None = None
) -> dict[str, Any]:
    config = dict(credential_manager.get_credentials("clickhouse"))
    warnings: list[str] = []

    # `client` defaults to a live ClickHouseCloudAPI built from the configured cloud_api credentials;
    # tests pass a substitute. With no credentials and no injected client there is nothing to enrich.
    api_cfg = config.get("cloud_api") or {}
    if client is None and api_cfg.get("key_id") and api_cfg.get("key_secret"):
        client = ClickHouseCloudAPI(api_cfg["key_id"], api_cfg["key_secret"])

    cloud_meta: dict | None = None
    if client is not None:
        try:
            cloud_meta = _fetch_cloud_metadata(config, warnings, client)
        # A malformed API response raises a structural error, not CloudAPIError; this step is optional,
        # so degrade gracefully rather than aborting the run.
        except (CloudAPIError, KeyError, IndexError, TypeError) as e:
            warnings.append(f"cloud_api: {str(e)[:200]}")

    if client is None:
        note = _NO_CREDS_NOTE
    elif cloud_meta is None:
        note = _API_FAIL_NOTE
    elif _has_billed_cost(cloud_meta):
        note = _ENRICHED_NOTE
    else:
        note = _ENRICHED_NO_COST_NOTE

    row = build_pricing_row(config, cloud_meta, note)
    save_to_duckdb(pd.DataFrame([row]), TABLE_NAME, db_path, schema=TABLE_SCHEMA)
    return {
        "status": "success",
        "message": f"ClickHouse Cloud cost enrichment complete (region={row['region_detected']}, "
        f"tier={row['tier']}, billed={'yes' if row['actual_billed_cost'] else 'no'})",
        "warnings": warnings,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    initialize_logging()
    _db_path, _creds_file = arguments_loader(desc="ClickHouse Cloud Cost Enrichment Step")
    try:
        payload = execute(
            credential_manager=create_credential_manager(PRODUCT_NAME, EnvGetter(), creds_path=_creds_file),
            db_path=_db_path,
        )
        print(json.dumps(payload))
    except Exception as exc:  # top-level: emit a structured error payload the pipeline can parse
        logger.error(f"ClickHouse Cloud cost enrichment failed: {exc}")
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        sys.exit(1)
