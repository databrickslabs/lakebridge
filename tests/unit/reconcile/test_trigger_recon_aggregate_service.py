"""Unit tests for TriggerReconAggregateService's UTC session-timezone handling.

``trigger_recon_aggregates`` shares ``TriggerReconService.create_recon_dependencies``
with the main ``trigger_recon`` entry point, so it inherits the same UTC pin for a
Redshift source (see ``_UTC_PINNING_REQUIRED_DIALECTS`` in ``trigger_recon_service``).
It must also restore the pre-recon session timezone once the recon completes, exactly
like ``trigger_recon`` does — otherwise the pin leaks past the recon on a shared /
interactive cluster for this entry point too.
"""

from databricks.labs.lakebridge.reconcile.trigger_recon_aggregate_service import TriggerReconAggregateService
from tests.unit.reconcile._trigger_recon_harness import make_reconcile_config, run_timezone_restore_scenario

_FINAL_OUTPUT = (
    "databricks.labs.lakebridge.reconcile.trigger_recon_aggregate_service.generate_final_reconcile_aggregate_output"
)


def test_trigger_recon_aggregates_restores_original_session_timezone_after_recon():
    """The UTC pin set by create_recon_dependencies must not outlive the aggregate
    recon on a shared/interactive cluster, exactly as for the main trigger_recon
    entry point."""
    _spark, session_tz = run_timezone_restore_scenario(
        entry_point=TriggerReconAggregateService.trigger_recon_aggregates,
        final_output_target=_FINAL_OUTPUT,
        config=make_reconcile_config(report_type="aggregate"),
    )
    assert session_tz["value"] == "America/New_York", "session timezone must be restored after the recon completes"


def test_trigger_recon_aggregates_does_not_touch_timezone_for_non_pinning_dialect():
    """A dialect outside ``_UTC_PINNING_REQUIRED_DIALECTS`` never pins, so there is
    nothing to restore — the finally block must be a no-op in that case."""
    spark, _session_tz = run_timezone_restore_scenario(
        entry_point=TriggerReconAggregateService.trigger_recon_aggregates,
        final_output_target=_FINAL_OUTPUT,
        config=make_reconcile_config(report_type="aggregate", source="snowflake"),
        pins=False,
    )
    spark.conf.set.assert_not_called()
