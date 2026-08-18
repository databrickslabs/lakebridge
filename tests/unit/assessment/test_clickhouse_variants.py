import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from databricks.labs.lakebridge.assessments import AUTO, SOURCE_SYSTEM_VARIANTS
from databricks.labs.lakebridge.assessments.profiler import get_pipeline
from databricks.labs.lakebridge.assessments.variants import resolve_clickhouse_variant
from databricks.labs.lakebridge.resources.assessments.clickhouse.cloud import cost_enrich

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


def _pricing_row(config: dict, cloud_meta: dict) -> dict:
    """Build a costs_pricing_config row via the Cloud enrichment step's pure row-builder.

    cost_enrich is the single optional Cloud python step; build_pricing_row is its pure core (no
    DuckDB, no live API), so tier precedence / billed-cost projection are exercised directly."""
    return cost_enrich.build_pricing_row(config, cloud_meta, note="test")


def _decode_billed(row: dict) -> dict:
    """The actual_billed_cost column is JSON-encoded into a VARCHAR; decode it back to a dict."""
    return json.loads(row["actual_billed_cost"])


def test_cost_enrich_config_tier_override_wins_over_cloud_api() -> None:
    """The configurator nests the plan-tier override under cloud_api.tier. It must win over the Cloud
    API organizationTier and be recorded with tier_source=config."""
    cloud_meta = {"tier": "scale", "tier_source": "usage_cost", "region_key": "aws:us-east-1"}
    row = _pricing_row({"cloud_api": {"tier": "enterprise"}}, cloud_meta)
    assert row["tier"] == "enterprise"
    assert row["tier_source"] == "config"


def test_cost_enrich_top_level_tier_override_also_honored() -> None:
    """A hand-written creds file may put the override at the top level; that is honored too."""
    cloud_meta = {"tier": "scale", "tier_source": "usage_cost", "region_key": "aws:us-east-1"}
    row = _pricing_row({"tier": "basic"}, cloud_meta)
    assert row["tier"] == "basic"
    assert row["tier_source"] == "config"


def test_cost_enrich_tier_falls_back_to_cloud_api_when_no_override() -> None:
    """With no config override, the Cloud API organizationTier drives the recorded tier."""
    cloud_meta = {"tier": "scale", "tier_source": "usage_cost", "region_key": "aws:us-east-1"}
    row = _pricing_row({}, cloud_meta)
    assert row["tier"] == "scale"
    assert row["tier_source"] == "usage_cost"


def test_cost_enrich_emits_no_actual_monthly_dollar_field() -> None:
    """The actual-billed-cost block must not carry a field that looks like an actual monthly bill.
    The 30-day normalization is a projection and must be explicitly named as such."""
    cloud_meta = {
        "tier": "scale",
        "tier_source": "usage_cost",
        "region_key": "aws:us-east-1",
        "actual_cost": {"record_count": 5, "actual_total_usd": 700.0, "org_total_usd": 900.0},
        "actual_cost_window_days": 7,
    }
    billed = _decode_billed(_pricing_row({}, cloud_meta))
    # The actual figures are present and untouched.
    assert billed["total_usd"] == 900.0
    assert billed["service_total_usd"] == 700.0
    # No plain "monthly_total_usd" that could be mistaken for a bill.
    assert "monthly_total_usd" not in billed
    # The projection exists but is explicitly labeled.
    assert "monthly_total_usd_projected" in billed
    assert billed["monthly_total_usd_projected"] == round(900.0 * (30 / 7), 2)


def _credential_manager(config: dict) -> MagicMock:
    cred_manager = MagicMock()
    cred_manager.get_credentials.return_value = config
    return cred_manager


def test_cost_enrich_execute_uses_injected_client(tmp_path) -> None:
    """execute() enriches from the injected Cloud API client (no live API/credentials needed) and
    writes the row via save_to_duckdb."""
    config = {"host": "svc.us-east-1.aws.clickhouse.cloud", "cloud_api": {"key_id": "k", "key_secret": "s"}}
    client = MagicMock(spec=cost_enrich.ClickHouseCloudAPI)
    client.discover_service.return_value = {"organization_id": "org", "service_id": "svc", "provider": "aws"}
    client.get_usage_cost.return_value = {"grandTotals": []}
    client.summarize_usage_cost.return_value = {"tier": "scale", "tier_as_of_date": "2026-08-01"}

    with patch.object(cost_enrich, "save_to_duckdb") as save_mock:
        result = cost_enrich.execute(_credential_manager(config), str(tmp_path / "extract.duckdb"), client=client)

    assert result["status"] == "success"
    assert client.discover_service.called
    save_mock.assert_called_once()


def test_cost_enrich_execute_without_credentials_writes_no_creds_note(tmp_path) -> None:
    """With no cloud_api credentials and no injected client, execute() still succeeds, writing a row
    that records why no billed-cost enrichment happened."""
    with patch.object(cost_enrich, "save_to_duckdb") as save_mock:
        result = cost_enrich.execute(_credential_manager({"host": "10.0.0.5"}), str(tmp_path / "extract.duckdb"))

    assert result["status"] == "success"
    written = save_mock.call_args.args[0].iloc[0]
    assert "no cloud_api credentials were configured" in written["note"]
    assert written["actual_billed_cost"] is None
