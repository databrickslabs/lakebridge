import logging
import pytest

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
