"""Exception hierarchy for the fingerprint module.

Lakebridge's fingerprint pre-check is opt-in and must never block the full reconcile
pipeline. The trigger layer catches these (plus external IO errors raised by Spark /
JDBC) and falls through to the legacy hash+JOIN path on any failure.
"""


class FingerprintError(Exception):
    """Base for errors raised by the fingerprint module."""


class UnsupportedDataSourceError(FingerprintError, ValueError):
    """No registered FingerprintQueryBuilder for the requested data source.

    Inherits ValueError for backwards compatibility — callers that ``except ValueError``
    on the dispatch lookup still catch this; new callers can target ``FingerprintError``.
    """


class UnmappedTargetColumnMappingError(FingerprintError):
    """A ``column_mapping`` entry references a target name that doesn't exist on the target.

    Raised by ``align_columns`` so the trigger layer can record
    ``IneligibilityReason.UNMAPPED_TARGET_COLUMN_MAPPING`` on
    ``recon_metrics.fingerprint_metrics.ineligibility_reason`` instead of a
    silent ``None`` fallback. The trigger catches this *before* the broader
    ``FingerprintError`` branch so the metric reports an ineligible verdict
    (a config issue), not a precheck failure.
    """
