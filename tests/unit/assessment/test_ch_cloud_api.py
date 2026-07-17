from databricks.labs.lakebridge.resources.assessments.clickhouse.cloud_api import ClickHouseCloudAPI


def _usage(grand_total, records):
    return {"grandTotalCHC": grand_total, "costs": records}


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


def test_summarize_org_total_none_when_grand_total_absent():
    """Falls back to None org total when the API returns no grandTotalCHC (caller then uses service sum)."""
    usage = _usage(
        grand_total=None,
        records=[{"serviceId": "svc-1", "date": "2026-07-16", "metrics": {"computeCHC": 0.5}}],
    )
    summary = ClickHouseCloudAPI.summarize_usage_cost(usage, service_id="svc-1")

    assert summary["actual_total_usd"] == 0.5
    assert summary["org_total_usd"] is None
