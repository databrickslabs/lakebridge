import io
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from databricks.labs.lakebridge.resources.assessments.clickhouse.cloud_api import ClickHouseCloudAPI, CloudAPIError


def _usage(grand_total, records):
    return {"grandTotalCHC": grand_total, "costs": records}


@contextmanager
def _fake_urlopen(body: bytes):
    """Patch urllib.request.urlopen so the response body reads back as ``body``."""

    @contextmanager
    def _open(_req, **_kwargs):  # absorbs the timeout= keyword urlopen is called with
        yield io.BytesIO(body)

    with patch(
        "databricks.labs.lakebridge.resources.assessments.clickhouse.cloud_api.urllib.request.urlopen",
        side_effect=_open,
    ):
        yield


def test_summarize_reports_org_total_as_tco():
    """org_total_usd is the org-wide grandTotalCHC (true TCO), which can exceed the profiled
    service's summed cost when org-level charges (backups/ClickPipes/shared) aren't service-attributed."""
    usage = _usage(
        grand_total=0.972,
        records=[
            {
                "serviceId": "svc-1",
                "date": "2026-07-16",
                "organizationTier": "SCALE",
                "metrics": {"computeCHC": 0.811, "storageCHC": 0.0},
            }
        ],
    )
    summary = ClickHouseCloudAPI.summarize_usage_cost(usage, service_id="svc-1")

    assert summary["actual_total_usd"] == 0.811  # per-service sum
    assert summary["org_total_usd"] == 0.972  # org-wide TCO (higher; includes org-level charges)
    assert summary["tier"] == "scale"


def test_summarize_includes_unnamed_metric_buckets_in_total():
    """A CHC bucket outside the named set (e.g. ClickPipes) is folded into `other` and the total,
    so the per-service billed cost is not silently under-reported."""
    usage = _usage(
        grand_total=1.0,
        records=[
            {
                "serviceId": "svc-1",
                "date": "2026-07-16",
                "metrics": {"computeCHC": 0.5, "storageCHC": 0.1, "clickPipesCHC": 0.4},
            }
        ],
    )
    summary = ClickHouseCloudAPI.summarize_usage_cost(usage, service_id="svc-1")

    assert summary["actual_cost_chc"]["other"] == 0.4  # the unnamed bucket
    assert summary["actual_total_usd"] == 1.0  # 0.5 + 0.1 + 0.4, not 0.6


def test_summarize_buckets_metrics_case_insensitively():
    """Metric keys are classified case-insensitively, so a re-cased key still lands in the right
    bucket (not silently folded into `other`), while the total stays complete."""
    usage = _usage(
        grand_total=1.0,
        records=[
            {
                "serviceId": "svc-1",
                "date": "2026-07-16",
                # Deliberately re-cased vs the canonical computeCHC / …DataTransferCHC keys.
                "metrics": {
                    "ComputeCHC": 0.5,
                    "storageCHC": 0.1,
                    "interRegionDataTransferCHC": 0.2,
                    "dictionaryCHC": 0.1,
                },
            }
        ],
    )
    summary = ClickHouseCloudAPI.summarize_usage_cost(usage, service_id="svc-1")

    breakdown = summary["actual_cost_chc"]
    assert breakdown["compute"] == 0.5  # "ComputeCHC" still recognized despite the capital C
    assert breakdown["storage"] == 0.1
    assert breakdown["data_transfer"] == 0.2  # matched by substring regardless of case/prefix
    assert breakdown["other"] == 0.1  # only the genuinely-unnamed bucket
    assert summary["actual_total_usd"] == 0.9  # total covers every metric


def test_summarize_org_total_none_when_grand_total_absent():
    """Falls back to None org total when the API returns no grandTotalCHC (caller then uses service sum)."""
    usage = _usage(
        grand_total=None,
        records=[{"serviceId": "svc-1", "date": "2026-07-16", "metrics": {"computeCHC": 0.5}}],
    )
    summary = ClickHouseCloudAPI.summarize_usage_cost(usage, service_id="svc-1")

    assert summary["actual_total_usd"] == 0.5
    assert summary["org_total_usd"] is None


def test_get_raises_cloud_api_error_on_non_json_body():
    """A non-JSON body (e.g. a proxy's HTML error page) surfaces as CloudAPIError, not a bare ValueError."""
    api = ClickHouseCloudAPI("key-id", "key-secret")
    with _fake_urlopen(b"<html>502 Bad Gateway</html>"):
        with pytest.raises(CloudAPIError, match="invalid JSON response"):
            api.list_organizations()


def test_get_raises_cloud_api_error_on_non_dict_payload():
    """A non-object JSON body (e.g. a bare list) surfaces as CloudAPIError, not AttributeError."""
    api = ClickHouseCloudAPI("key-id", "key-secret")
    with _fake_urlopen(b"[1, 2, 3]"):
        with pytest.raises(CloudAPIError, match="unexpected response shape"):
            api.list_organizations()


def test_discover_service_matches_host_case_insensitively():
    """A configured host differing only in case from the endpoint host still resolves that service."""
    api = ClickHouseCloudAPI("key-id", "key-secret")
    services = [
        {"id": "svc-a", "endpoints": [{"protocol": "https", "host": "abc.us-east-1.aws.clickhouse.cloud"}]},
        {"id": "svc-b", "endpoints": [{"protocol": "https", "host": "xyz.us-west-2.aws.clickhouse.cloud"}]},
    ]
    with patch.object(api, "list_services", return_value=services):
        meta = api.discover_service(org_id="org-1", host="ABC.US-EAST-1.AWS.CLICKHOUSE.CLOUD")
    assert meta["service_id"] == "svc-a"
