"""Building blocks for recon config auto-discovery.

Two protocols define the two-stage shape of any discovery pass:

- `TableDiscoverer.discover()` — schema-level; produces table pairs.
- `TableAutoConfigurer.configure()` — per-Table; fills in column mappings,
  transformations, join columns, etc.

`TableMatcher` is the only `TableDiscoverer` today. `ColumnMappingAutoConfigurer`
is the only `TableAutoConfigurer` today; transformations / join keys /
thresholds will arrive as additional `TableAutoConfigurer` classes.

Adding a new configurer: see `SUPPORTED_AUTO_CONFIGURERS` in `execute.py`.
"""

from __future__ import annotations
from typing import Protocol
import dataclasses
import logging
import re

from databricks.labs.lakebridge.config import TableRecon
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.recon_config import ColumnMapping, Table

logger = logging.getLogger(__name__)


class TableDiscoverer(Protocol):
    def discover(
        self,
        *,
        source: DataSource,
        source_catalog: str,
        source_schema: str,
        target: DataSource,
        target_catalog: str,
        target_schema: str,
    ) -> TableRecon: ...


class IdentifierMatchingStrategy(Protocol):
    """Pluggable name-matching strategy used by `TableMatcher` and `ColumnMappingAutoConfigurer`."""

    def match_all(self, source_names: list[str], candidate_names: list[str]) -> dict[str, str | None]: ...


class NormalizedMatcher:
    """Match names by trying progressively looser normalisations.

    For each normalisation step the matcher builds a lookup from normalised
    candidate -> original candidate. If a source name normalises to the same
    form as exactly one candidate at that step, it's a match.
    """

    DELIMITER_RE = re.compile(r"[-\s]+")

    def match_all(self, source_names: list[str], candidate_names: list[str]) -> dict[str, str | None]:
        results: dict[str, str | None] = {}
        remaining = list(candidate_names)

        for src in source_names:
            matched = self._match_one(src, remaining)
            results[src] = matched
            if matched is not None:
                remaining.remove(matched)

        return results

    @classmethod
    def _match_one(cls, source_name: str, candidates: list[str]) -> str | None:
        src_forms = cls.normalize_steps(source_name)
        candidate_forms = [(cand, cls.normalize_steps(cand)) for cand in candidates]

        for step, src_norm in enumerate(src_forms):
            matches = [cand for cand, forms in candidate_forms if forms[step] == src_norm]
            if len(matches) == 1:
                return matches[0]
        return None

    @classmethod
    def normalize_steps(cls, name: str) -> list[str]:
        """Return progressively more aggressive normalisations of `name`.

        Steps:
        0. trim + lowercase
        1. unify delimiters (kebab / spaces -> underscore)
        2. collapse all underscores (`emp_id` -> `empid`)
        3. naive singularise (strip trailing s/es/ies)
        """
        form = name.strip().lower()
        forms = [form]
        form = cls.DELIMITER_RE.sub("_", form)
        forms.append(form)
        form = form.replace("_", "")
        forms.append(form)
        form = cls.naive_singularize(form)
        forms.append(form)
        return forms

    @staticmethod
    def naive_singularize(word: str) -> str:
        """Best-effort singularisation for English table/column names.

        Rules (applied in order):
        - `ies` -> `y`  (categories -> category)
        - `ses` -> `s`  (addresses -> address)
        - `s`   -> ``   (employees -> employee)
        """
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("ses"):
            return word[:-2]
        if word.endswith("s"):
            return word[:-1]
        return word


class TableMatcher:
    """`TableDiscoverer` impl: discovers source/target table pairs by matching table names."""

    def __init__(self, strategy: IdentifierMatchingStrategy = NormalizedMatcher()) -> None:
        self._strategy = strategy

    def discover(
        self,
        *,
        source: DataSource,
        source_catalog: str,
        source_schema: str,
        target: DataSource,
        target_catalog: str,
        target_schema: str,
    ) -> TableRecon:
        source_tables = source.list_tables(source_catalog, source_schema)
        target_tables = target.list_tables(target_catalog, target_schema)

        table_name_mapping = self._strategy.match_all(source_tables, target_tables)

        tables: list[Table] = []
        unmatched: list[str] = []
        for src_table in source_tables:
            tgt_table = table_name_mapping[src_table]
            if tgt_table is None:
                unmatched.append(src_table)
                continue
            tables.append(Table(source_name=src_table, target_name=tgt_table))

        if unmatched:
            unmatched_str = ", ".join(unmatched)
            logger.warning(f"Could not auto-match {len(unmatched)} source table(s); add manually: {unmatched_str}")

        return TableRecon(tables=tables)


class TableAutoConfigurer(Protocol):
    def configure(
        self,
        *,
        table: Table,
        source: DataSource,
        source_catalog: str,
        source_schema: str,
        target: DataSource,
        target_catalog: str,
        target_schema: str,
    ) -> Table: ...


class ColumnMappingAutoConfigurer:
    """`TableAutoConfigurer` impl: fills `Table.column_mapping` by matching column names.

    Only emits a `ColumnMapping` entry when source and target column names differ —
    identically-named columns don't need an explicit mapping. Unmatched source
    columns are logged for manual review.
    """

    def __init__(self, strategy: IdentifierMatchingStrategy = NormalizedMatcher()) -> None:
        self._strategy = strategy

    def configure(
        self,
        *,
        table: Table,
        source: DataSource,
        source_catalog: str,
        source_schema: str,
        target: DataSource,
        target_catalog: str,
        target_schema: str,
    ) -> Table:
        source_columns = source.get_schema(source_catalog, source_schema, table.source_name)
        target_columns = target.get_schema(target_catalog, target_schema, table.target_name)

        source_names = [c.column_name for c in source_columns]
        target_names = [c.column_name for c in target_columns]

        name_mapping = self._strategy.match_all(source_names, target_names)

        mappings: list[ColumnMapping] = []
        unmatched: list[str] = []
        for src_col in source_names:
            tgt_col = name_mapping[src_col]
            if tgt_col is None:
                unmatched.append(src_col)
                continue
            if src_col != tgt_col:
                mappings.append(ColumnMapping(source_name=src_col, target_name=tgt_col))

        if unmatched:
            unmatched_str = ", ".join(unmatched)
            logger.warning(
                f"Could not auto-match {len(unmatched)} column(s) for "
                f"{table.source_name} -> {table.target_name}: {unmatched_str}"
            )

        return dataclasses.replace(table, column_mapping=mappings or None)
