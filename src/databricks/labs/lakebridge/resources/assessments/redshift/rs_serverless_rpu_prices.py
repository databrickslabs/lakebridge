"""Fetch the current Amazon Redshift Serverless RPU price for the cluster's AWS region.

Uses the public AWS Price List bulk API (no credentials / IAM) so it works wherever the
profiler has outbound HTTPS. Writes ``rs_serverless_rpu_prices(region, usd_per_rpu_hour,
valid_at)`` into the profiler DuckDB, with ``valid_at`` = the run date, so a downstream
cost join can pick the right snapshot: serverless cost = rpu_hours * usd_per_rpu_hour[region].

Serverless-only: provisioned clusters bill per node-hour and are priced separately.
Best-effort — the pipeline step is ``optional``, so a missing region / price / network
failure surfaces as ABSENT rather than failing the profile.
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


def write_prices(db_path: str, cred_config_path: str) -> str:
    """Resolve the region, fetch its RPU price, and write the one-row price table. Raises on failure."""
    region = resolve_region(cred_config_path)
    if not region:
        raise RuntimeError("No AWS region resolved (creds metadata.region or host); cannot price RPU-hours.")
    price = fetch_rpu_price(region)
    if price is None:
        raise RuntimeError(f"No on-demand Serverless RPU price found for region {region!r} in the AWS Price List API.")
    with connect_to_profiler_db(db_path) as conn:
        conn.execute(
            "CREATE OR REPLACE TABLE rs_serverless_rpu_prices "
            "(region VARCHAR, usd_per_rpu_hour DOUBLE, valid_at DATE)"
        )
        conn.execute("INSERT INTO rs_serverless_rpu_prices VALUES (?, ?, ?)", [region, price, date.today()])
    return f"rs_serverless_rpu_prices: {region} = ${price}/RPU-hr valid {date.today()}"


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
