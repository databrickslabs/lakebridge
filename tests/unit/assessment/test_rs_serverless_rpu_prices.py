"""Unit tests for the Redshift Serverless RPU price step (live AWS Price List API + bundled fallback)."""

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


def _rows(db):
    with duckdb.connect(str(db)) as conn:
        return conn.execute(
            "SELECT region, usd_per_rpu_hour, valid_at, source FROM rs_serverless_rpu_prices"
        ).fetchall()


def test_fetch_rpu_price_picks_on_demand_compute_rate():
    with mock.patch.object(mod.requests, "get", side_effect=_fake_get):
        assert mod.fetch_rpu_price("us-east-1") == 0.375  # not 0.293 reservation, not storage


def test_fetch_rpu_price_unknown_region_returns_none():
    with mock.patch.object(mod.requests, "get", side_effect=_fake_get):
        assert mod.fetch_rpu_price("mars-west-1") is None


def test_write_prices_uses_live_api(tmp_path):
    db = tmp_path / "p.duckdb"
    with (
        mock.patch.object(mod, "resolve_region", return_value="us-east-1"),
        mock.patch.object(mod.requests, "get", side_effect=_fake_get),
    ):
        mod.write_prices(str(db), "unused")
    assert _rows(db) == [("us-east-1", 0.375, date.today(), "api")]


def test_write_prices_falls_back_on_api_exception(tmp_path):
    db = tmp_path / "p.duckdb"
    with (
        mock.patch.object(mod, "resolve_region", return_value="us-west-2"),
        mock.patch.object(mod, "fetch_rpu_price", side_effect=RuntimeError("network down")),
    ):
        mod.write_prices(str(db), "unused")
    assert _rows(db) == [("us-west-2", 0.36, mod._FALLBACK_VALID_AT, "fallback")]


def test_write_prices_falls_back_when_api_returns_none(tmp_path):
    db = tmp_path / "p.duckdb"
    with (
        mock.patch.object(mod, "resolve_region", return_value="eu-west-1"),
        mock.patch.object(mod, "fetch_rpu_price", return_value=None),
    ):
        mod.write_prices(str(db), "unused")
    assert _rows(db) == [("eu-west-1", 0.387, mod._FALLBACK_VALID_AT, "fallback")]


def test_write_prices_raises_without_region():
    with mock.patch.object(mod, "resolve_region", return_value=None):
        try:
            mod.write_prices("unused-db", "unused")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "region" in str(e).lower()


def test_write_prices_raises_when_no_api_and_no_fallback(tmp_path):
    with (
        mock.patch.object(mod, "resolve_region", return_value="zz-nowhere-1"),
        mock.patch.object(mod, "fetch_rpu_price", side_effect=RuntimeError("down")),
    ):
        try:
            mod.write_prices(str(tmp_path / "p.duckdb"), "unused")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "no rpu price" in str(e).lower()
