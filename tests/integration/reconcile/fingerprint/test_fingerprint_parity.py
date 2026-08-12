"""Integration parity tests for the fingerprint pre-check.

Contract under test: with ``reconcile_optimizer=True`` the reconcile result
must be **identical** to the legacy (``reconcile_optimizer=False``) path on
real data — same mismatch / missing-in-source / missing-in-target counts — and
the pre-check must have actually engaged (not silently fallen back to the
legacy pipeline).

Workspace-gated: like ``test_recon_redshift_job_succeeds`` this needs a live
Databricks workspace (``spark`` via databricks-connect + ``ws``), the
``sandbox_labs_tool_redshift`` UC connection, and the sandbox
``labs.lakebridge.diamonds`` Redshift table. It runs in the project integration
CI, not the ``make test`` unit gate (there is no local Spark in unit CI).

The assertions deliberately avoid hard-coding the sandbox row contents: parity
("ON agrees with OFF") holds whether the diamonds source and target happen to
match or differ, so the test stays robust to sandbox data drift while still
proving the pre-check produces the legacy answer.
"""

import dataclasses
import logging

import pytest

from databricks.sdk.service.catalog import SchemaInfo

from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon
from databricks.labs.lakebridge.reconcile.fingerprint.metadata import INELIGIBLE_FLAG_DISABLED
from databricks.labs.lakebridge.reconcile.normalize_recon_config_service import NormalizeReconConfigService
from databricks.labs.lakebridge.reconcile.recon_config import Table
from databricks.labs.lakebridge.reconcile.recon_output_config import DataReconcileOutput
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService

logger = logging.getLogger(__name__)


def _run_recon_one(ws, spark, recon_config: ReconcileConfig, table_conf: Table, *, fingerprint: bool):
    """Run one table reconcile in-process and return ``(DataReconcileOutput, FingerprintRunMetadata)``.

    Uses ``do_recon_one`` rather than ``recon_one`` because it returns the
    reconcile output **without** writing the audit tables and, crucially,
    surfaces the fingerprint metadata so the test can prove the pre-check
    engaged on the ON run.
    """
    cfg = dataclasses.replace(recon_config, reconcile_optimizer=fingerprint)
    reconciler, _capture = TriggerReconService.create_recon_dependencies(ws, spark, cfg)
    normalized = NormalizeReconConfigService(reconciler.source, reconciler.target).normalize_recon_table_config(
        table_conf
    )
    _schema_out, data_out, _dur, fp_meta = TriggerReconService.do_recon_one(
        reconciler, cfg, normalized, recon_id="fingerprint-parity-test"
    )
    return data_out, fp_meta


def _assert_counts_equal(fp_out: DataReconcileOutput, legacy_out: DataReconcileOutput) -> None:
    assert (
        fp_out.mismatch_count == legacy_out.mismatch_count
    ), f"mismatch_count: fingerprint={fp_out.mismatch_count} legacy={legacy_out.mismatch_count}"
    assert (
        fp_out.missing_in_src_count == legacy_out.missing_in_src_count
    ), f"missing_in_src_count: fingerprint={fp_out.missing_in_src_count} legacy={legacy_out.missing_in_src_count}"
    assert (
        fp_out.missing_in_tgt_count == legacy_out.missing_in_tgt_count
    ), f"missing_in_tgt_count: fingerprint={fp_out.missing_in_tgt_count} legacy={legacy_out.missing_in_tgt_count}"


@pytest.mark.timeout(func_only=True)
def test_fingerprint_parity_matches_legacy_redshift(
    ws,
    spark,
    redshift_recon_config: ReconcileConfig,
    redshift_recon_table_config: TableRecon,
) -> None:
    """Baseline: ON ≡ OFF on the unmutated diamonds source/target, and the
    pre-check engaged (verdict set, no fallback) on the ON run."""
    table_conf = redshift_recon_table_config.tables[0]

    off_out, off_meta = _run_recon_one(ws, spark, redshift_recon_config, table_conf, fingerprint=False)
    on_out, on_meta = _run_recon_one(ws, spark, redshift_recon_config, table_conf, fingerprint=True)

    _assert_counts_equal(on_out, off_out)

    # The pre-check actually decided this run (did not fall back to legacy).
    assert on_meta.eligible is True
    assert on_meta.verdict in {"MATCH", "MISMATCH"}, f"unexpected verdict {on_meta.verdict!r}"
    assert on_meta.fallback_to_full_pipeline is False, "fingerprint fell back to the legacy pipeline"

    # The OFF run must not have run the pre-check at all.
    assert off_meta.eligible is False
    assert off_meta.ineligibility_reason == INELIGIBLE_FLAG_DISABLED


@pytest.mark.timeout(func_only=True)
def test_fingerprint_parity_detects_same_mismatch_after_target_mutation(
    ws,
    spark,
    recon_schema: SchemaInfo,
    redshift_recon_config: ReconcileConfig,
    redshift_recon_table_config: TableRecon,
) -> None:
    """Force a value difference on exactly one target row, then assert both
    modes report the *same* counts and the pre-check resolves to MISMATCH
    without falling back. Mutating the Databricks target (which the test owns)
    keeps the shared Redshift sandbox untouched."""
    table_conf = redshift_recon_table_config.tables[0]
    target_fqn = f"{recon_schema.catalog_name}.{recon_schema.name}.{table_conf.target_name}"

    # (color='E', clarity='SI2') is a single row in the diamonds fixture; bump a
    # non-key column so the row mismatches on value, not on existence.
    spark.sql(f"UPDATE {target_fqn} SET carat = carat + 1.0 WHERE color = 'E' AND clarity = 'SI2'")

    off_out, _off_meta = _run_recon_one(ws, spark, redshift_recon_config, table_conf, fingerprint=False)
    on_out, on_meta = _run_recon_one(ws, spark, redshift_recon_config, table_conf, fingerprint=True)

    _assert_counts_equal(on_out, off_out)
    assert on_out.mismatch_count >= 1, "expected at least the mutated row to mismatch"

    assert on_meta.eligible is True
    assert on_meta.verdict == "MISMATCH"
    assert on_meta.fallback_to_full_pipeline is False
