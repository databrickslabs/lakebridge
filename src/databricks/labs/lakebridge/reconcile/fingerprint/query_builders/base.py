from abc import ABC, abstractmethod

from databricks.labs.lakebridge.reconcile.recon_config import Schema


class FingerprintQueryBuilder(ABC):
    """Dialect-specific SQL generation for fingerprint detection and row fetch."""

    def __init__(self, treat_empty_as_null: bool = False):
        # Default False matches the row-hash convention in expression_generator
        # (TRIM does not collapse '' to NULL); flipping silently disagrees with row-hash
        # on every NULL <-> '' flip.
        self._treat_empty_as_null = treat_empty_as_null

    @abstractmethod
    def build_detection_sql(
        self,
        schema: str,
        table: str,
        columns: list[Schema],
        column_mapping: dict[str, str] | None,
        sub_bucket_count: int,
        bucket_count: int,
    ) -> str:
        """Source-side detection SQL grouping into sub-buckets with (cnt, p1, p2, p1_rh2, p2_rh2)."""

    @abstractmethod
    def build_source_filter_subquery(
        self,
        schema: str,
        table: str,
        columns: list[Schema],
        sub_bucket_count: int,
        solved_hashes: dict[int, list[int]],
        unsolved_sb_ids: list[int],
    ) -> str:
        """Subquery selecting only rows in solved sub-buckets / unsolved sub-buckets.

        Returns ``(SELECT * FROM schema.table WHERE <predicate>) _fp_filtered``,
        suitable for replacing ``:tbl`` in a HashQueryBuilder query.
        """

    @abstractmethod
    def serialize_column(self, col_name: str, col_type: str) -> str:
        """Cast the column to a deterministic string representation for MD5 hashing."""
