"""Shared helpers for the trigger-recon UTC session-timezone tests.

Both trigger entry points (``TriggerReconService.trigger_recon`` and
``TriggerReconAggregateService.trigger_recon_aggregates``) inherit the same
Redshift UTC pin from ``create_recon_dependencies`` and must restore the
pre-recon timezone afterwards. Centralising the config builder and the restore
harness here keeps the two test modules from duplicating them (pylint
``duplicate-code`` / R0801).
"""

from unittest.mock import MagicMock, patch

from databricks.labs.lakebridge.config import (
    ReconcileConfig,
    SourceConnectionConfig,
    TargetConnectionConfig,
)
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService

ORIGINAL_TZ = "America/New_York"


def make_reconcile_config(*, report_type: str = "data", source: str = "redshift", **extra) -> ReconcileConfig:
    """ReconcileConfig with the standard source/target used across the trigger tests."""
    return ReconcileConfig(
        report_type=report_type,
        source=SourceConnectionConfig(dialect=source, catalog="dev", schema="src", uc_connection_name="conn"),
        target=TargetConnectionConfig(catalog="tc", schema="ts"),
        metadata_config=MagicMock(),
        **extra,
    )


def run_timezone_restore_scenario(*, entry_point, final_output_target: str, config: ReconcileConfig, pins: bool = True):
    """Drive ``entry_point`` with a Spark mock that tracks ``spark.sql.session.timeZone``.

    When ``pins`` is True the stubbed ``create_recon_dependencies`` mimics
    ``pin_utc_session`` flipping the session to UTC (Redshift source); when False it
    leaves the timezone untouched (non-pinning dialect). Returns ``(spark, session_tz)``
    so the caller can assert on the final timezone / that ``set`` was never called.
    """
    session_tz = {"value": ORIGINAL_TZ}
    spark = MagicMock()
    spark.conf.get.side_effect = lambda key, default=None: session_tz["value"]
    spark.conf.set.side_effect = lambda key, value: session_tz.__setitem__("value", value)

    reconciler, recon_capture = MagicMock(), MagicMock()

    def _fake_create_deps(_ws, _spark, _cfg):
        if pins:
            _spark.conf.set("spark.sql.session.timeZone", "UTC")
        return reconciler, recon_capture

    with (
        patch.object(TriggerReconService, "create_recon_dependencies", side_effect=_fake_create_deps),
        patch.object(TriggerReconService, "verify_successful_reconciliation", return_value=MagicMock()),
        patch(final_output_target),
    ):
        entry_point(MagicMock(), spark, MagicMock(tables=[]), config)

    return spark, session_tz
