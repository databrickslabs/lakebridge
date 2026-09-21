"""Fetch the current Amazon Redshift Serverless RPU price for the cluster's AWS region.

Primary source is the public AWS Price List bulk API (no credentials / IAM), queried per run
so the price is current. If that call fails (no network, region not listed, parse error) the
step falls back to a bundled snapshot captured on ``_FALLBACK_VALID_AT`` from the same API, so
the extract always carries a price. Writes ``rs_serverless_rpu_prices(region, usd_per_rpu_hour,
valid_at, source)`` into the profiler DuckDB, where ``source`` is ``'api'`` (``valid_at`` = run
date) or ``'fallback'`` (``valid_at`` = snapshot date). Downstream:
    serverless cost = rs_serverless_usage.rpu_hours * usd_per_rpu_hour[region]

Serverless-only: provisioned clusters bill per node-hour and are priced separately.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import requests

from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.connections.redshift_utils import parse_redshift_region
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import connect_to_profiler_db

_PRICING_HOST = "https://pricing.us-east-1.amazonaws.com"
_REGION_INDEX = "/offers/v1.0/aws/AmazonRedshift/current/region_index.json"

# Bundled fallback: on-demand Serverless RPU price (USD/RPU-Hr) captured from the AWS Price List
# API on this date. Used only when the live query fails; refresh both together.
_FALLBACK_VALID_AT = date(2026, 9, 21)
_FALLBACK_PRICES = {
    "us-east-2": 0.36,
    "us-west-2": 0.36,
    "eu-north-1": 0.374,
    "us-gov-west-1": 0.375,
    "us-east-1": 0.375,
    "us-gov-east-1": 0.375,
    "eu-west-1": 0.387,
    "eu-south-2": 0.387,
    "mx-central-1": 0.39375,
    "ap-southeast-7": 0.405,
    "ap-southeast-5": 0.405,
    "il-central-1": 0.406,
    "eu-west-3": 0.406,
    "eu-south-1": 0.40635,
    "ca-central-1": 0.4125,
    "ca-west-1": 0.4125,
    "me-central-1": 0.4158,
    "ap-southeast-4": 0.419,
    "ap-southeast-2": 0.419,
    "ap-south-2": 0.4275,
    "ap-south-1": 0.4275,
    "ap-northeast-2": 0.438,
    "ap-southeast-6": 0.43995,
    "ap-east-2": 0.4446,
    "ap-southeast-1": 0.45,
    "ap-southeast-3": 0.45,
    "ap-northeast-3": 0.45,
    "eu-central-1": 0.451,
    "af-south-1": 0.46053,
    "eu-west-2": 0.467,
    "us-west-1": 0.469,
    "ap-northeast-1": 0.494,
    "ap-east-1": 0.495,
    "eu-central-2": 0.4961,
    "sa-east-1": 0.5976,
}


def resolve_region(cred_config_path: str) -> str | None:
    """Region the configurator parsed from the endpoint host (creds ``metadata.region``), else the host."""
    try:
        config = create_credential_manager(
            "redshift", EnvGetter(), creds_path=Path(cred_config_path)
        ).get_credentials("redshift")
    except Exception:  # pylint: disable=broad-except  # creds are optional / may be malformed
        return None
    if not isinstance(config, dict):
        return None
    metadata = config.get("metadata")
    if isinstance(metadata, dict) and metadata.get("region"):
        return str(metadata["region"])
    host = config.get("host")
    return parse_redshift_region(str(host)) if host else None


def fetch_rpu_price(region: str) -> float | None:
    """On-demand Redshift Serverless RPU-hour price (USD) for ``region`` from the AWS Price List API.

    The on-demand compute rate is productFamily ``Serverless`` with usagetype ending
    ``:ServerlessUsage``; reservation rates carry a ``-CR-...`` suffix and are excluded.
    """
    region_index = requests.get(_PRICING_HOST + _REGION_INDEX, timeout=30).json()
    entry = region_index.get("regions", {}).get(region)
    if not entry:
        return None
    offer = requests.get(_PRICING_HOST + entry["currentVersionUrl"], timeout=120).json()
    on_demand = offer.get("terms", {}).get("OnDemand", {})
    for sku, product in offer.get("products", {}).items():
        if product.get("productFamily") != "Serverless":
            continue
        if not str(product.get("attributes", {}).get("usagetype", "")).endswith(":ServerlessUsage"):
            continue
        for term in on_demand.get(sku, {}).values():
            for dimension in term.get("priceDimensions", {}).values():
                usd = dimension.get("pricePerUnit", {}).get("USD")
                if usd is not None:
                    return float(usd)
    return None


def _price_for(region: str) -> tuple[float | None, date | None, str | None]:
    """(price, valid_at, source): the live API price ('api', valid today) if reachable, else the bundled fallback."""
    try:
        price = fetch_rpu_price(region)
        if price is not None:
            return price, date.today(), "api"
    except Exception:  # pylint: disable=broad-except  # network/parse failure -> use the bundled fallback
        pass
    fallback = _FALLBACK_PRICES.get(region)
    if fallback is not None:
        return fallback, _FALLBACK_VALID_AT, "fallback"
    return None, None, None


def write_prices(db_path: str, cred_config_path: str) -> str:
    """Resolve the region, price it (live API else fallback), and write the one-row table. Raises if unpriceable."""
    region = resolve_region(cred_config_path)
    if not region:
        raise RuntimeError("No AWS region resolved (creds metadata.region or host); cannot price RPU-hours.")
    price, valid_at, source = _price_for(region)
    if price is None:
        raise RuntimeError(f"No RPU price for region {region!r} (live API failed and no bundled fallback).")
    with connect_to_profiler_db(db_path) as conn:
        conn.execute(
            "CREATE OR REPLACE TABLE rs_serverless_rpu_prices "
            "(region VARCHAR, usd_per_rpu_hour DOUBLE, valid_at DATE, source VARCHAR)"
        )
        conn.execute("INSERT INTO rs_serverless_rpu_prices VALUES (?, ?, ?, ?)", [region, price, valid_at, source])
    return f"rs_serverless_rpu_prices: {region} = ${price}/RPU-hr ({source}, valid {valid_at})"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--credential-config-path", required=True)
    args = parser.parse_args()
    try:
        message = write_prices(args.db_path, args.credential_config_path)
        print(json.dumps({"status": "success", "message": message}))
    except Exception as e:  # pylint: disable=broad-except  # optional step: report and let the pipeline mark ABSENT
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
