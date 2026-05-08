import logging
from unittest.mock import MagicMock, patch

import pytest

from databricks.labs.lakebridge.config import (
    ReconcileConfig,
    ReconcileMetadataConfig,
    SourceConnectionConfig,
    TableRecon,
    TargetConnectionConfig,
)
from databricks.labs.lakebridge.reconcile.exception import ReconciliationException
from databricks.labs.lakebridge.reconcile.recon_output_config import ReconcileTableOutput, ReconcileOutput, StatusOutput
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService


def test_success_no_mismatches_and_no_exceptions(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    results = [
        ReconcileTableOutput("t1", "s1", StatusOutput(column=True, row=True, schema=True)),
        ReconcileTableOutput("t2", "s2", StatusOutput(column=True, row=True, schema=True)),
    ]
    reconcile_output = ReconcileOutput(recon_id="mock-id", results=results)

    returned = TriggerReconService.verify_successful_reconciliation(reconcile_output, report_type="daily")

    assert returned is reconcile_output
    assert any("completed successfully" in rec.message for rec in caplog.records)


def test_mismatches_but_no_exceptions_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)

    results = [
        ReconcileTableOutput("t1", "s1", StatusOutput(column=True, row=True, schema=True, aggregate=True)),
        ReconcileTableOutput("t2", "s2", StatusOutput(column=False, row=True, schema=True, aggregate=None)),
    ]
    reconcile_output = ReconcileOutput(recon_id="mock-id", results=results)

    returned = TriggerReconService.verify_successful_reconciliation(reconcile_output, report_type="daily")

    assert returned is reconcile_output
    assert any("found mismatches in 1 table(s)" in rec.message for rec in caplog.records)


def test_ignores_none_status_values() -> None:
    # None should be ignored (not treated as mismatch)
    results = [
        ReconcileTableOutput("t1", "s1", StatusOutput(column=None, row=None, schema=None, aggregate=None)),
    ]
    reconcile_output = ReconcileOutput(recon_id="mock-id", results=results)

    # Should not raise
    TriggerReconService.verify_successful_reconciliation(reconcile_output, report_type="daily")


def test_raises_on_exception_message() -> None:
    results = [
        ReconcileTableOutput("t1", "s1", StatusOutput(column=True, row=True, schema=True, aggregate=True)),
        ReconcileTableOutput(
            "t2",
            "s2",
            StatusOutput(column=True, row=True, schema=True, aggregate=True),
            exception_message="Something went wrong",
        ),
    ]
    reconcile_output = ReconcileOutput(recon_id="mock-id", results=results)

    with pytest.raises(ReconciliationException) as excinfo:
        TriggerReconService.verify_successful_reconciliation(reconcile_output, report_type="all")

    assert "Reconciliation **all** with id: mock-id failed with exceptions for" in str(excinfo.value)


def _build_aggregate_reconcile_config() -> ReconcileConfig:
    return ReconcileConfig(
        report_type="aggregate",
        source=SourceConnectionConfig(dialect="databricks", catalog="cat", schema="src"),
        target=TargetConnectionConfig(catalog="cat", schema="tgt"),
        metadata_config=ReconcileMetadataConfig(catalog="cat", schema="recon", volume="vol"),
    )


def test_trigger_recon_dispatches_aggregate_to_aggregate_service() -> None:
    """report_type='aggregate' must be forwarded to TriggerReconAggregateService.

    Otherwise _do_recon_one falls through both schema/data branches, no comparison
    runs, and recon_capture.start() records the run with status=true.
    """
    table_recon = TableRecon(tables=[])
    reconcile_config = _build_aggregate_reconcile_config()
    ws = MagicMock()
    spark = MagicMock()
    expected = ReconcileOutput(recon_id="agg-id", results=[])

    target = "databricks.labs.lakebridge.reconcile.trigger_recon_aggregate_service.TriggerReconAggregateService.trigger_recon_aggregates"
    with patch(target, return_value=expected) as mock_aggregate:
        with patch.object(TriggerReconService, "create_recon_dependencies") as mock_deps:
            result = TriggerReconService.trigger_recon(
                ws=ws,
                spark=spark,
                table_recon=table_recon,
                reconcile_config=reconcile_config,
            )

    mock_aggregate.assert_called_once()
    _, kwargs = mock_aggregate.call_args
    assert kwargs["reconcile_config"] is reconcile_config
    assert kwargs["table_recon"] is table_recon
    # Aggregate path must not touch the data-path setup
    mock_deps.assert_not_called()
    assert result is expected


@pytest.mark.parametrize("report_type", ["schema", "data", "row", "all"])
def test_trigger_recon_does_not_dispatch_non_aggregate(report_type: str) -> None:
    """Non-aggregate report types keep the original path and never reach the aggregate service."""
    reconcile_config = _build_aggregate_reconcile_config()
    reconcile_config.report_type = report_type

    target = "databricks.labs.lakebridge.reconcile.trigger_recon_aggregate_service.TriggerReconAggregateService.trigger_recon_aggregates"
    with (
        patch(target) as mock_aggregate,
        patch.object(TriggerReconService, "create_recon_dependencies") as mock_deps,
        patch.object(TriggerReconService, "verify_successful_reconciliation") as mock_verify,
        patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.generate_final_reconcile_output"),
    ):
        mock_deps.return_value = (MagicMock(), MagicMock(intermediate_persist=MagicMock(base_dir="/tmp")))
        mock_verify.return_value = ReconcileOutput(recon_id="x", results=[])
        ws = MagicMock()
        TriggerReconService.trigger_recon(
            ws=ws,
            spark=MagicMock(),
            table_recon=TableRecon(tables=[]),
            reconcile_config=reconcile_config,
        )

    mock_aggregate.assert_not_called()
