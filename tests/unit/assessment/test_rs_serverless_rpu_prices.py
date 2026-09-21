"""Unit tests for the Redshift Serverless RPU price fetch step (AWS Price List API, mocked)."""

from datetime import date
from unittest import mock

import duckdb

from databricks.labs.lakebridge.resources.assessments.redshift import rs_serverless_rpu_prices as mod

_REGION_INDEX = {"regions": {"us-east-1": {"currentVersionUrl": "/offers/v1.0/aws/AmazonRedshift/x/us-east-1/index.json"}}}
_OFFER = {
    "products": {
        # on-demand compute rate (the one we want)
        "SKU_RPU": {"productFamily": "Serverless", "attributes": {"usagetype": "USE1-Redshift:ServerlessUsage"}},
        # reservation rate (must be excluded)
        "SKU_RESV": {"productFamily": "Serverless", "attributes": {"usagetype": "USE1-Redshift:ServerlessUsage-CR-1YR-NU"}},
        # managed storage (different metric, must be excluded)
        "SKU_RMS": {"productFamily": "Redshift Managed Storage", "attributes": {"usagetype": "USE1-RMS:Serverless"}},
    },
    "terms": {
        "OnDemand": {
            "SKU_RPU": {"t1": {"priceDimensions": {"d1": {"pricePerUnit": {"USD": "0.3750000000"}}}}},
            "SKU_RESV": {"t2": {"priceDimensions": {"d2": {"pricePerUnit": {"USD": "0.2930000000"}}}}},
        }
    },
}


def _fake_get(url, timeout=None):
    resp = mock.Mock()
    resp.json.return_value = _OFFER if "us-east-1/index.json" in url else _REGION_INDEX
    return resp


def test_fetch_rpu_price_picks_on_demand_compute_rate():
    with mock.patch.object(mod.requests, "get", side_effect=_fake_get):
        # 0.375 on-demand, not 0.293 reservation, not the storage rate
        assert mod.fetch_rpu_price("us-east-1") == 0.375


def test_fetch_rpu_price_unknown_region_returns_none():
    with mock.patch.object(mod.requests, "get", side_effect=_fake_get):
        assert mod.fetch_rpu_price("mars-west-1") is None


def test_write_prices_writes_one_row_table(tmp_path):
    db = tmp_path / "p.duckdb"
    with (
        mock.patch.object(mod, "resolve_region", return_value="us-east-1"),
        mock.patch.object(mod.requests, "get", side_effect=_fake_get),
    ):
        mod.write_prices(str(db), "unused-creds.yml")
    with duckdb.connect(str(db)) as conn:
        rows = conn.execute("SELECT region, usd_per_rpu_hour, valid_at FROM rs_serverless_rpu_prices").fetchall()
    assert rows == [("us-east-1", 0.375, date.today())]


def test_write_prices_raises_without_region(tmp_path):
    with mock.patch.object(mod, "resolve_region", return_value=None):
        try:
            mod.write_prices(str(tmp_path / "p.duckdb"), "unused-creds.yml")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "region" in str(e).lower()
