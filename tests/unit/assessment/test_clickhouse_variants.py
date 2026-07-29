from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from databricks.labs.lakebridge.assessments import SOURCE_SYSTEM_VARIANTS, AUTO
from databricks.labs.lakebridge.assessments.profiler import get_pipeline
from databricks.labs.lakebridge.assessments.variants import resolve_clickhouse_variant
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.costs import CostsCollector

# clickhouse is registered as AUTO in the registry; these are the resolver's outputs / config directories.
CLICKHOUSE_VARIANTS = ("oss", "cloud")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLICKHOUSE_RESOURCES = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments/clickhouse"

_PROBE_SQL = "SELECT value FROM system.settings WHERE name = 'cloud_mode'"


def test_clickhouse_is_registered_as_auto_variant_source() -> None:
    assert SOURCE_SYSTEM_VARIANTS["clickhouse"] == (AUTO,)


@pytest.mark.parametrize("variant", CLICKHOUSE_VARIANTS)
def test_clickhouse_variants_have_pipeline_config_path(variant: str) -> None:
    cfg_path = get_pipeline("clickhouse", variant)
    assert f"/clickhouse/{variant}/pipeline_config.yml" in str(cfg_path)


@pytest.mark.parametrize("variant", CLICKHOUSE_VARIANTS)
def test_clickhouse_variant_config_references_existing_files(variant: str) -> None:
    """Every extract_source referenced by a variant config must point at a real script."""
    config = yaml.safe_load((_CLICKHOUSE_RESOURCES / variant / "pipeline_config.yml").read_text())
    for step in config["steps"]:
        extract_source = _REPO_ROOT / step["extract_source"]
        assert extract_source.exists(), f"{variant} step '{step['name']}' references missing file {extract_source}"


@pytest.mark.parametrize(
    ("cloud_mode_value", "expected"),
    [
        ("1", "cloud"),
        ("true", "cloud"),
        ("0", "oss"),
        ("", "oss"),
    ],
)
def test_resolve_clickhouse_variant_probes_cloud_mode(cloud_mode_value: str, expected: str) -> None:
    """With a non-cloud host, the cloud_mode server setting decides oss vs cloud."""
    db_manager = MagicMock()
    db_manager.__enter__.return_value = db_manager
    rows = [[cloud_mode_value]] if cloud_mode_value != "" else []
    db_manager.fetch.return_value = MagicMock(rows=rows)
    with (
        patch("databricks.labs.lakebridge.assessments.variants.create_connector", return_value=db_manager),
        patch("databricks.labs.lakebridge.assessments.variants.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = {"host": "10.0.0.5"}
        assert resolve_clickhouse_variant(Path("creds.yml")) == expected
    db_manager.fetch.assert_called_once_with(_PROBE_SQL)


def test_resolve_clickhouse_variant_cloud_host_short_circuits() -> None:
    """A *.clickhouse.cloud host resolves to cloud without probing the server."""
    db_manager = MagicMock()
    db_manager.__enter__.return_value = db_manager
    with (
        patch("databricks.labs.lakebridge.assessments.variants.create_connector", return_value=db_manager),
        patch("databricks.labs.lakebridge.assessments.variants.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = {
            "host": "abc123.us-east-1.aws.clickhouse.cloud",
        }
        assert resolve_clickhouse_variant(Path("creds.yml")) == "cloud"
    db_manager.fetch.assert_not_called()


def test_resolve_clickhouse_variant_lookalike_host_is_not_cloud() -> None:
    """A host that merely CONTAINS 'clickhouse.cloud' (not a real suffix) must NOT be treated as
    Cloud by the suffix short-circuit -> it falls through to the cloud_mode probe (regression: the
    resolver and the extract's cloud detection must agree, both use endswith)."""
    db_manager = MagicMock()
    db_manager.__enter__.return_value = db_manager
    db_manager.fetch.return_value = MagicMock(rows=[["0"]])  # on-prem -> oss
    with (
        patch("databricks.labs.lakebridge.assessments.variants.create_connector", return_value=db_manager),
        patch("databricks.labs.lakebridge.assessments.variants.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = {"host": "my-clickhouse.cloudco.internal"}
        assert resolve_clickhouse_variant(Path("creds.yml")) == "oss"
    db_manager.fetch.assert_called_once_with(_PROBE_SQL)


def test_costs_cloud_detection_matches_resolver_via_public_collect() -> None:
    """The costs collector's Cloud detection agrees with the resolver: a lookalike host that merely
    contains 'clickhouse.cloud' (cloud_mode=0) is treated as OSS -> pricing_config.is_cloud is False,
    no actual_billed_cost. Exercised through the public collect() path (no protected-member access)."""
    conn = MagicMock()
    # Empty result for every query keeps collect() cheap; cloud_mode=0 drives the OSS branch.
    conn.query.return_value = [{"value": "0"}]
    collector = CostsCollector(conn=conn, config={"host": "my-clickhouse.cloudco.internal", "days_back": 7})
    results = collector.collect()

    assert results["pricing_config"]["is_cloud"] is False
    assert "actual_billed_cost" not in results["pricing_config"]
    assert results["resource_footprint"]["deployment"] == "self-managed (OSS)"


def _cloud_costs_collector(config: dict, cloud_meta: dict) -> CostsCollector:
    """A CostsCollector on the Cloud branch (is_cloud=True) with the Cloud API metadata stubbed.

    Subclasses to override the Cloud API fetch so the test needs no live API and no protected-member
    assignment.
    """

    class _StubbedCostsCollector(CostsCollector):
        def _get_cloud_metadata(self) -> dict:
            return cloud_meta

    conn = MagicMock()
    conn.query.return_value = []  # keep the bucket queries cheap/empty
    cfg = {"days_back": 7, "is_cloud": True, **config}
    return _StubbedCostsCollector(conn=conn, config=cfg)


def test_costs_config_tier_override_wins_over_cloud_api() -> None:
    """Blocking #4: the configurator nests the plan-tier override under cloud_api.tier. It must win
    over the Cloud API organizationTier and be recorded with tier_source=config (was silently
    dropped: only top-level `tier` was read, so a configured override came out as tier=None)."""
    cloud_meta = {"tier": "scale", "tier_source": "usage_cost", "region_key": "aws:us-east-1"}
    collector = _cloud_costs_collector({"cloud_api": {"tier": "enterprise"}}, cloud_meta)
    results = collector.collect()
    assert results["pricing_config"]["tier"] == "enterprise"
    assert results["pricing_config"]["tier_source"] == "config"


def test_costs_top_level_tier_override_also_honored() -> None:
    """A hand-written creds file may put the override at the top level; that is honored too."""
    cloud_meta = {"tier": "scale", "tier_source": "usage_cost", "region_key": "aws:us-east-1"}
    collector = _cloud_costs_collector({"tier": "basic"}, cloud_meta)
    results = collector.collect()
    assert results["pricing_config"]["tier"] == "basic"
    assert results["pricing_config"]["tier_source"] == "config"


def test_costs_tier_falls_back_to_cloud_api_when_no_override() -> None:
    """With no config override, the Cloud API organizationTier drives the recorded tier."""
    cloud_meta = {"tier": "scale", "tier_source": "usage_cost", "region_key": "aws:us-east-1"}
    collector = _cloud_costs_collector({}, cloud_meta)
    results = collector.collect()
    assert results["pricing_config"]["tier"] == "scale"
    assert results["pricing_config"]["tier_source"] == "usage_cost"


def test_costs_emits_no_actual_monthly_dollar_field() -> None:
    """Blocking #3: the actual-billed-cost block must not carry a field that looks like an actual
    monthly bill. The 30-day normalization is a projection and must be explicitly named as such."""
    cloud_meta = {
        "tier": "scale",
        "tier_source": "usage_cost",
        "region_key": "aws:us-east-1",
        "actual_cost": {"record_count": 5, "actual_total_usd": 700.0, "org_total_usd": 900.0},
        "actual_cost_window_days": 7,
    }
    collector = _cloud_costs_collector({}, cloud_meta)
    results = collector.collect()
    billed = results["pricing_config"]["actual_billed_cost"]
    # The actual figures are present and untouched.
    assert billed["total_usd"] == 900.0
    assert billed["service_total_usd"] == 700.0
    # No plain "monthly_total_usd" that could be mistaken for a bill.
    assert "monthly_total_usd" not in billed
    # The projection exists but is explicitly labeled.
    assert "monthly_total_usd_projected" in billed
    assert billed["monthly_total_usd_projected"] == round(900.0 * (30 / 7), 2)
