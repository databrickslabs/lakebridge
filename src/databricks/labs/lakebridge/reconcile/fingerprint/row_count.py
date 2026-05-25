"""Target row-count fetcher for adaptive sub-bucket tier selection.

Tier selection needs an order-of-magnitude row count, never a full scan. A SELECT
COUNT(*) on a billion-row table defeats fingerprint mode entirely. The chain is
metadata-only:

1. Explicit user override — highest precedence.
2. Target Delta ``numRecords`` from DESCRIBE DETAIL — free, exact, sub-second.
3. Static default — fall through with a warning.

Source-side row counts (Redshift catalog stats) are not consulted: source and target
must use the same tier, picking from target Delta metadata is enough at order-of-
magnitude resolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from pyspark.sql import SparkSession
from pyspark.sql.utils import AnalysisException

logger = logging.getLogger(__name__)


class RowCountSource(str, Enum):
    """Provenance of the row count used for tier selection."""

    USER_OVERRIDE = "user_override"
    DELTA_DESCRIBE_DETAIL = "delta_describe_detail"
    STATIC_DEFAULT = "static_default"


@dataclass(frozen=True)
class RowCountResult:
    """``row_count`` is None only when ``source == STATIC_DEFAULT``."""

    row_count: int | None
    source: RowCountSource


def fetch_target_row_count(
    spark: SparkSession,
    *,
    catalog: str | None,
    schema: str,
    table: str,
    override_row_count: int | None = None,
) -> RowCountResult:
    """Resolve the target row count via metadata-only paths.

    Never raises: every failure logs and falls through. Tier selection is a best-effort
    optimisation and must not block detection.
    """
    if override_row_count is not None and override_row_count > 0:
        logger.info(f"fingerprint.tier.row_count_source=user_override row_count={override_row_count}")
        return RowCountResult(row_count=override_row_count, source=RowCountSource.USER_OVERRIDE)

    fully_qualified = _build_fqn(catalog=catalog, schema=schema, table=table)

    delta_count = _try_describe_detail(spark, fully_qualified)
    if delta_count is not None:
        logger.info(
            f"fingerprint.tier.row_count_source=delta_describe_detail "
            f"table={fully_qualified} row_count={delta_count}"
        )
        return RowCountResult(row_count=delta_count, source=RowCountSource.DELTA_DESCRIBE_DETAIL)

    logger.warning(
        f"fingerprint.tier.row_count_source=static_default table={fully_qualified} — DESCRIBE DETAIL "
        "returned no numRecords (target may be non-Delta or stats missing); falling back. "
        "Set ReconcileConfig.fingerprint_row_count_override to a non-zero estimate "
        "to pin the tier explicitly."
    )
    return RowCountResult(row_count=None, source=RowCountSource.STATIC_DEFAULT)


def _build_fqn(*, catalog: str | None, schema: str, table: str) -> str:
    if catalog:
        return f"{catalog}.{schema}.{table}"
    return f"{schema}.{table}"


def _try_describe_detail(spark: SparkSession, fully_qualified_name: str) -> int | None:
    """Run DESCRIBE DETAIL and return numRecords when available.

    Returns None when the table is not Delta, the column is missing, the value is null,
    or any Spark-side error occurs — tier selection must never block detection.
    """
    try:
        detail_df = spark.sql(f"DESCRIBE DETAIL {fully_qualified_name}")
    except AnalysisException as exc:
        logger.debug(f"DESCRIBE DETAIL failed for {fully_qualified_name}: {exc}")
        return None
    except Exception as exc:  # pylint: disable=broad-exception-caught  # tier-selection must never block detection
        logger.debug(f"DESCRIBE DETAIL raised unexpected error for {fully_qualified_name}: {exc}")
        return None

    if "numRecords" not in detail_df.columns:
        logger.debug(f"DESCRIBE DETAIL on {fully_qualified_name} returned no numRecords column")
        return None

    rows = detail_df.select("numRecords").collect()
    if not rows:
        logger.debug(f"DESCRIBE DETAIL on {fully_qualified_name} returned 0 rows")
        return None

    num_records = rows[0]["numRecords"]
    if num_records is None:
        logger.debug(f"DESCRIBE DETAIL on {fully_qualified_name} returned NULL numRecords")
        return None

    if not isinstance(num_records, int) or num_records < 0:
        logger.debug(f"DESCRIBE DETAIL on {fully_qualified_name} returned unexpected numRecords value {num_records!r}")
        return None

    return num_records
