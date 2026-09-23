"""Persistence-side metadata for the fingerprint pre-check.

Lives in its own module so ``recon_capture`` can import the dataclass without a circular
import via ``fingerprint.orchestrator``. The dataclass and enum values here are part of
the public Delta schema for ``recon_metrics.fingerprint_metrics`` — keep value strings
stable; renaming any of them breaks downstream dashboards.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    # Avoid circular import: ``orchestrator`` already imports from this module.
    from databricks.labs.lakebridge.reconcile.fingerprint.orchestrator import FingerprintResult


class IneligibilityReason(str, Enum):
    """Why a table was skipped by the fingerprint pre-check.

    Values surface to ``recon_metrics.fingerprint_metrics.ineligibility_reason``.
    Adding new members is additive; renaming or removing breaks dashboards.
    """

    FLAG_DISABLED = "flag_disabled"
    UNSUPPORTED_DIALECT = "unsupported_dialect"
    REPORT_TYPE_NOT_DATA = "report_type_not_data"
    NO_JOIN_COLUMNS = "no_join_columns"
    FILTERS_CONFIGURED = "filters_configured"
    TRANSFORMS_CONFIGURED = "transforms_configured"
    COLUMN_THRESHOLDS_CONFIGURED = "column_thresholds_configured"
    TABLE_THRESHOLDS_CONFIGURED = "table_thresholds_configured"
    # ``classify_ineligibility`` runs before the precheck without a target schema,
    # so it cannot detect this. ``align_columns`` discovers it once ``tgt_schema``
    # is in hand and the trigger layer routes the typed exception through
    # ``ineligible(...)`` so an adoption query against
    # ``recon_metrics.fingerprint_metrics.ineligibility_reason`` can quantify how
    # often a typo or a column-mapping drift skips the precheck.
    UNMAPPED_TARGET_COLUMN_MAPPING = "unmapped_target_column_mapping"


# Module-level aliases preserved so existing callers keep importing by name.
INELIGIBLE_FLAG_DISABLED = IneligibilityReason.FLAG_DISABLED.value
INELIGIBLE_UNSUPPORTED_DIALECT = IneligibilityReason.UNSUPPORTED_DIALECT.value
INELIGIBLE_REPORT_TYPE_NOT_DATA = IneligibilityReason.REPORT_TYPE_NOT_DATA.value
INELIGIBLE_NO_JOIN_COLUMNS = IneligibilityReason.NO_JOIN_COLUMNS.value
INELIGIBLE_FILTERS_CONFIGURED = IneligibilityReason.FILTERS_CONFIGURED.value
INELIGIBLE_TRANSFORMS_CONFIGURED = IneligibilityReason.TRANSFORMS_CONFIGURED.value
INELIGIBLE_COLUMN_THRESHOLDS_CONFIGURED = IneligibilityReason.COLUMN_THRESHOLDS_CONFIGURED.value
INELIGIBLE_TABLE_THRESHOLDS_CONFIGURED = IneligibilityReason.TABLE_THRESHOLDS_CONFIGURED.value
INELIGIBLE_UNMAPPED_TARGET_COLUMN_MAPPING = IneligibilityReason.UNMAPPED_TARGET_COLUMN_MAPPING.value


class FetchPath(str, Enum):
    """Stage-2 source-fetch strategy used for one run.

    Values surface to ``recon_metrics.fingerprint_metrics.fetch_path``.
    """

    V1_SANDWICH = "v1_sandwich"
    # Historical: persisted by 0.12.4-0.12.7. Kept so old recon_metrics rows continue to
    # round-trip through code that imports the constant; current code never emits it.
    V2_REDSHIFT_SPLIT = "v2_redshift_split"


FETCH_PATH_V1_SANDWICH = FetchPath.V1_SANDWICH.value
FETCH_PATH_V2_REDSHIFT_SPLIT = FetchPath.V2_REDSHIFT_SPLIT.value


# Verdict surfaced to recon_metrics.fingerprint_metrics.verdict; a Literal so mypy
# catches typos at edit time. None means "ineligible / disabled / pre-detection".
RunVerdict = Literal["MATCH", "MISMATCH", "FAILED"]


@dataclass(frozen=True)
class FingerprintRunMetadata:
    """Recorded once per (recon_id, table) on recon_metrics.

    Always written, even for ineligible / disabled runs, so adoption-style queries on
    recon_metrics.fingerprint_metrics don't need a LEFT-JOIN to count opt-outs.

    Field semantics:

    - ``eligible``: did the pre-check actually run?
    - ``ineligibility_reason``: an IneligibilityReason value when ``eligible=False``.
    - ``verdict``: ``"MATCH"`` / ``"MISMATCH"`` / ``"FAILED"`` / None when ineligible.
    - ``elapsed_ms``: detection-phase wall-clock; 0 when skipped.
    - ``solved_count`` / ``unsolved_sb_count`` / ``total_mismatched_sbs``: solver telemetry
      for tuning sub-bucket sizing.
    - ``fallback_to_full_pipeline``: True when fingerprint was eligible but didn't
      short-circuit (systemic mismatch, missing rows, exception, soft skip).
    - ``sub_bucket_count`` / ``bucket_count``: the adaptive tier for this run; 0 when
      the pre-check did not run.
    - ``target_row_count``: target Delta row count from DESCRIBE DETAIL, or override.
      None when both fell through to the static default.
    - ``row_count_source``: provenance — one of the RowCountSource values, or None.
    - ``fetch_path``: name of the Stage-2 source-fetch strategy. None on MATCH or
      ineligible. See FetchPath for stable values.
    """

    eligible: bool = False
    ineligibility_reason: str | None = None
    verdict: RunVerdict | None = None
    elapsed_ms: int = 0
    solved_count: int = 0
    unsolved_sb_count: int = 0
    total_mismatched_sbs: int = 0
    fallback_to_full_pipeline: bool = False
    sub_bucket_count: int = 0
    bucket_count: int = 0
    target_row_count: int | None = None
    row_count_source: str | None = None
    fetch_path: str | None = None

    @classmethod
    def ineligible(cls, reason: str) -> "FingerprintRunMetadata":
        return cls(eligible=False, ineligibility_reason=reason)

    @classmethod
    def disabled(cls) -> "FingerprintRunMetadata":
        """Default for non-fingerprint reconciles, so the persisted struct stays uniform."""
        return cls(eligible=False, ineligibility_reason=INELIGIBLE_FLAG_DISABLED)

    @classmethod
    def from_result(
        cls,
        result: "FingerprintResult",
        *,
        verdict: RunVerdict,
        fallback_to_full_pipeline: bool = False,
    ) -> "FingerprintRunMetadata":
        """Single-site mapping from detection-side ``FingerprintResult`` to the persisted
        metadata. Adding a new telemetry field is one line here, not three copy-loop edits
        across the orchestrator's MATCH / MISMATCH-fallback / MISMATCH-success branches.
        """
        return cls(
            eligible=True,
            verdict=verdict,
            elapsed_ms=result.detection_elapsed_ms,
            solved_count=result.solved_count,
            unsolved_sb_count=result.unsolved_sb_count,
            total_mismatched_sbs=result.total_mismatched_sbs,
            fallback_to_full_pipeline=fallback_to_full_pipeline,
            sub_bucket_count=result.sub_bucket_count,
            bucket_count=result.bucket_count,
            target_row_count=result.target_row_count,
            row_count_source=result.row_count_source,
            fetch_path=result.fetch_path,
        )

    @classmethod
    def fallback(cls, *, verdict: RunVerdict | None = None) -> "FingerprintRunMetadata":
        """Eligible but no usable ``FingerprintResult`` (precheck declined or raised).

        ``verdict`` is ``"FAILED"`` when the precheck raised, ``None`` when it declined
        for a non-error reason (column-resolution skip, systemic mismatch, no solved
        buckets) — keeping the verdict unset on the latter so dashboards can distinguish.
        """
        return cls(eligible=True, fallback_to_full_pipeline=True, verdict=verdict)
