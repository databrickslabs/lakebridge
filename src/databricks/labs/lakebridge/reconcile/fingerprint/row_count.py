"""Target row-count fetcher for adaptive sub-bucket tier selection.

Tier selection needs only an order-of-magnitude row count, and it must be cheap. The
chain is metadata-first:

1. Target Delta ``numRecords`` from DESCRIBE DETAIL — free and exact WHERE EXPOSED.
   NOTE: current Databricks runtimes (e.g. DBR 17.3) do NOT expose a ``numRecords``
   column on ``DESCRIBE DETAIL`` (only ``numFiles`` / ``sizeInBytes``), so this path
   returns None there and step 2 supplies the count. Kept because it is free on any
   runtime that does expose it.
2. ``SELECT COUNT(*)`` — on a Delta target this is answered from the transaction-log
   file stats (physical plan is a ``LocalTableScan``: metadata-only, sub-second, exact
   even at 1M+ rows), so it does NOT scan the data. This is the working row-count source
   on Databricks. Only a non-Delta target, or a Delta target whose files lack row-count
   stats, would fall back to a real count — a bounded, one-time cost that is still
   strictly cheaper than the full row-hash reconcile fingerprint replaces. (The old
   premise that "SELECT COUNT(*) on a billion-row table defeats fingerprint mode" does
   not hold for Delta.)
3. Static default — fall through with a warning.

Source-side row counts (Redshift catalog stats) are not consulted: source and target
must use the same tier, and target Delta metadata is enough at order-of-magnitude
resolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from pyspark.sql import SparkSession
from pyspark.sql.utils import AnalysisException

from databricks.labs.lakebridge.reconcile.fingerprint.constants import quote_identifier

# Spark SQL identifier delimiter; kept local (not imported from ``spark_target``) so this
# metadata-only module has no dependency on the hashing layer.
_SPARK_IDENTIFIER_QUOTE = "`"

logger = logging.getLogger(__name__)


class RowCountSource(str, Enum):
    """Provenance of the row count used for tier selection."""

    DELTA_DESCRIBE_DETAIL = "delta_describe_detail"
    COUNT_STAR = "count_star"
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
) -> RowCountResult:
    """Resolve the target row count via cheap, metadata-first paths.

    Tries DESCRIBE DETAIL ``numRecords`` (free where a runtime exposes it), then
    ``SELECT COUNT(*)`` (metadata-only on Delta — see module docstring), then the static
    default. Never raises: every failure logs and falls through. Tier selection is a
    best-effort optimisation and must not block detection.
    """
    fully_qualified = _build_fqn(catalog=catalog, schema=schema, table=table)

    delta_count = _try_describe_detail(spark, fully_qualified)
    if delta_count is not None:
        logger.info(
            f"fingerprint.tier.row_count_source=delta_describe_detail "
            f"table={fully_qualified} row_count={delta_count}"
        )
        return RowCountResult(row_count=delta_count, source=RowCountSource.DELTA_DESCRIBE_DETAIL)

    # DESCRIBE DETAIL exposes no ``numRecords`` on current Databricks runtimes (DBR 17.3),
    # so this is the path that actually fires there. COUNT(*) on Delta is metadata-only
    # (a LocalTableScan over transaction-log file stats) — exact and sub-second, not the
    # full scan the original design feared.
    count_star = _try_count_star(spark, fully_qualified)
    if count_star is not None:
        logger.info(
            f"fingerprint.tier.row_count_source=count_star table={fully_qualified} row_count={count_star}"
        )
        return RowCountResult(row_count=count_star, source=RowCountSource.COUNT_STAR)

    logger.warning(
        f"fingerprint.tier.row_count_source=static_default table={fully_qualified} — DESCRIBE DETAIL "
        "exposed no numRecords and SELECT COUNT(*) was unavailable (target may be non-Delta, "
        "missing, or unreadable); falling back to the default tier."
    )
    return RowCountResult(row_count=None, source=RowCountSource.STATIC_DEFAULT)


def _build_fqn(*, catalog: str | None, schema: str, table: str) -> str:
    """Backtick-quote each identifier so a delimiting-needed name (hyphen, reserved word,
    embedded dot) cannot malform the ``DESCRIBE DETAIL`` SQL. Parity with
    ``spark_target._table_fqn``; without it such a name makes DESCRIBE DETAIL fail, the
    error is swallowed here, and tier selection silently degrades to the static default.
    """
    parts = [
        quote_identifier(schema, _SPARK_IDENTIFIER_QUOTE),
        quote_identifier(table, _SPARK_IDENTIFIER_QUOTE),
    ]
    if catalog:
        parts.insert(0, quote_identifier(catalog, _SPARK_IDENTIFIER_QUOTE))
    return ".".join(parts)


def _try_describe_detail(spark: SparkSession, fully_qualified_name: str) -> int | None:
    """Run DESCRIBE DETAIL and return numRecords when available.

    Returns None when the table is not Delta, the column is missing, the value is null,
    or any Spark-side error occurs — tier selection must never block detection.
    """
    # The whole read is inside the try: ``spark.sql`` may analyze cleanly and still fail
    # later at ``.collect()`` (a transient executor / IO error while materialising the
    # metadata row). Leaving the collect outside the guard would let that propagate and
    # break this function's "Never raises" contract, aborting the pre-check instead of
    # degrading to the static-default tier.
    try:
        detail_df = spark.sql(f"DESCRIBE DETAIL {fully_qualified_name}")
        if "numRecords" not in detail_df.columns:
            logger.debug(f"DESCRIBE DETAIL on {fully_qualified_name} returned no numRecords column")
            return None
        rows = detail_df.select("numRecords").collect()
    except AnalysisException as exc:
        logger.debug(f"DESCRIBE DETAIL failed for {fully_qualified_name}: {exc}")
        return None
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException as exc:  # tier-selection must never block detection
        logger.debug(f"DESCRIBE DETAIL raised unexpected error for {fully_qualified_name}: {exc}")
        return None

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


def _try_count_star(spark: SparkSession, fully_qualified_name: str) -> int | None:
    """Run ``SELECT COUNT(*)`` and return the count, or None on any failure (never raises).

    On a Delta target this is metadata-only: Delta answers COUNT(*) from the per-file
    ``numRecords`` in the transaction log (physical plan is a ``LocalTableScan``), so it is
    sub-second and exact even at 1M+ rows and does NOT scan the data. Mirrors
    ``_try_describe_detail``'s "never raises" contract — every error degrades to the
    static-default tier rather than aborting the pre-check. The whole read (including
    ``.collect()``) is inside the guard: ``spark.sql`` may analyze cleanly and still fail
    when the result is materialised.
    """
    try:
        rows = spark.sql(f"SELECT COUNT(*) AS cnt FROM {fully_qualified_name}").collect()
    except AnalysisException as exc:
        logger.debug(f"SELECT COUNT(*) failed for {fully_qualified_name}: {exc}")
        return None
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException as exc:  # tier-selection must never block detection
        logger.debug(f"SELECT COUNT(*) raised unexpected error for {fully_qualified_name}: {exc}")
        return None

    if not rows:
        logger.debug(f"SELECT COUNT(*) on {fully_qualified_name} returned 0 rows")
        return None

    count = rows[0]["cnt"]
    if count is None:
        logger.debug(f"SELECT COUNT(*) on {fully_qualified_name} returned NULL")
        return None

    if not isinstance(count, int) or count < 0:
        logger.debug(f"SELECT COUNT(*) on {fully_qualified_name} returned unexpected value {count!r}")
        return None

    return count
