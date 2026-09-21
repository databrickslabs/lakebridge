"""Unit tests for the Redshift Serverless RPU price step (live AWS Price List API + bundled fallback)."""

from datetime import date
from unittest import mock

import duckdb

from databricks.labs.lakebridge.resources.assessments.redshift import rs_serverless_rpu_prices as mod

_REGION_INDEX = {
    "regions": {"us-east-1": {"currentVersionUrl": "/offers/v1.0/aws/AmazonRedshift/x/us-east-1/index.json"}}
}
_OFFER = {
    "products": {
        # on-demand compute rate (the one we want)
        "SKU_RPU": {"productFamily": "Serverless", "attributes": {"usagetype": "USE1-Redshift:ServerlessUsage"}},
        # reservation rate (must be excluded by usagetype suffix)
        "SKU_RESV": {
            "productFamily": "Serverless",
            "attributes": {"usagetype": "USE1-Redshift:ServerlessUsage-CR-1YR-NU"},
        },
        # managed storage (different productFamily, must be excluded)
        "SKU_RMS": {"productFamily": "Redshift Managed Storage", "attributes": {"usagetype": "USE1-RMS:Serverless"}},
    },
    "terms": {
        "OnDemand": {
            "SKU_RPU": {
                "t1": {
                    "priceDimensions": {
                        # RPU-Hr compute rate (the one we want)
                        "d1": {"unit": "RPU-Hr", "pricePerUnit": {"USD": "0.3750000000"}},
                        # a non-RPU-Hr dimension on the same SKU must be ignored
                        "d_bytes": {"unit": "GB-Mo", "pricePerUnit": {"USD": "0.0230000000"}},
                    }
                }
            },
            "SKU_RESV": {
                "t2": {"priceDimensions": {"d2": {"unit": "RPU-Hr", "pricePerUnit": {"USD": "0.2930000000"}}}}
            },
        }
    },
}


def _fake_get(url, **_kwargs):
    resp = mock.Mock()
    resp.json.return_value = _OFFER if "us-east-1/index.json" in url else _REGION_INDEX
    return resp


def _rows(db_path):
    with duckdb.connect(str(db_path)) as conn:
        return conn.execute(
            "SELECT region, usd_per_rpu_hour, fetched_at, source FROM rs_serverless_rpu_prices"
        ).fetchall()


def test_fetch_rpu_price_picks_on_demand_compute_rate():
    with mock.patch.object(mod.requests, "get", side_effect=_fake_get):
        # 0.375 RPU-Hr rate: not 0.293 reservation, not storage, not the GB-Mo dimension
        assert mod.fetch_rpu_price("us-east-1") == 0.375


def test_fetch_rpu_price_unknown_region_returns_none():
    with mock.patch.object(mod.requests, "get", side_effect=_fake_get):
        assert mod.fetch_rpu_price("mars-west-1") is None


def test_fetch_rpu_price_raises_when_matching_prices_disagree():
    offer = {
        "products": {
            "SKU_A": {"productFamily": "Serverless", "attributes": {"usagetype": "USE1-Redshift:ServerlessUsage"}},
            "SKU_B": {"productFamily": "Serverless", "attributes": {"usagetype": "USW2-Redshift:ServerlessUsage"}},
        },
        "terms": {
            "OnDemand": {
                "SKU_A": {"t": {"priceDimensions": {"d": {"unit": "RPU-Hr", "pricePerUnit": {"USD": "0.375"}}}}},
                "SKU_B": {"t": {"priceDimensions": {"d": {"unit": "RPU-Hr", "pricePerUnit": {"USD": "0.999"}}}}},
            }
        },
    }

    def _get(url, **_kwargs):
        resp = mock.Mock()
        resp.json.return_value = offer if "us-east-1/index.json" in url else _REGION_INDEX
        return resp

    with mock.patch.object(mod.requests, "get", side_effect=_get):
        try:
            mod.fetch_rpu_price("us-east-1")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "0.375" in str(e) and "0.999" in str(e)


def test_write_prices_uses_live_api(tmp_path):
    db_path = tmp_path / "p.duckdb"
    with (
        mock.patch.object(mod, "resolve_region", return_value="us-east-1"),
        mock.patch.object(mod.requests, "get", side_effect=_fake_get),
    ):
        mod.write_prices(str(db_path), "unused")
    assert _rows(db_path) == [("us-east-1", 0.375, date.today(), "api")]


def test_write_prices_falls_back_on_api_exception(tmp_path):
    db_path = tmp_path / "p.duckdb"
    with (
        mock.patch.object(mod, "resolve_region", return_value="us-west-2"),
        mock.patch.object(mod, "fetch_rpu_price", side_effect=mod.requests.ConnectionError("network down")),
    ):
        mod.write_prices(str(db_path), "unused")
    # us-west-2 fallback = 0.36, stamped with the snapshot date and marked 'fallback'
    assert _rows(db_path) == [("us-west-2", 0.36, date(2026, 9, 21), "fallback")]


def test_write_prices_falls_back_when_api_returns_none(tmp_path):
    db_path = tmp_path / "p.duckdb"
    with (
        mock.patch.object(mod, "resolve_region", return_value="eu-west-1"),
        mock.patch.object(mod, "fetch_rpu_price", return_value=None),
    ):
        mod.write_prices(str(db_path), "unused")
    assert _rows(db_path) == [("eu-west-1", 0.387, date(2026, 9, 21), "fallback")]


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
        mock.patch.object(mod, "fetch_rpu_price", side_effect=mod.requests.ConnectionError("down")),
    ):
        try:
            mod.write_prices(str(tmp_path / "p.duckdb"), "unused")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "no rpu price" in str(e).lower()
