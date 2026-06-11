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
from databricks.labs.lakebridge.reconcile.recon_config import ColumnMapping, Schema, Table

logger = logging.getLogger(__name__)


class IdentifierMatchingStrategy(Protocol):
    def match_all(self, source_names: list[str], candidate_names: list[str]) -> dict[str, str | None]: ...


class NormalizedMatcher(IdentifierMatchingStrategy):
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
        - `ies`  -> `y`     (categories -> category)
        - `sses` -> `ss`    (addresses -> address, classes -> class)
        - `ss`   -> `ss`    (address, class — preserved, not stripped to `addres`/`clas`)
        - `s`    -> ``      (houses -> house, employees -> employee)
        """
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("sses"):
            return word[:-2]
        if word.endswith("ss"):
            return word
        if word.endswith("s"):
            return word[:-1]
        return word


class TableMatcher:
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


@dataclasses.dataclass(frozen=True)
class AutoConfigureContext:
    source: DataSource
    source_catalog: str
    source_schema: str
    source_columns: list[Schema]
    target: DataSource
    target_catalog: str
    target_schema: str
    target_columns: list[Schema]


class TableAutoConfigurer(Protocol):
    def configure(self, table: Table, ctx: AutoConfigureContext) -> Table: ...


class ColumnMappingAutoConfigurer(TableAutoConfigurer):
    """`TableAutoConfigurer` impl: fills `Table.column_mapping`, `Table.select_columns`, `Table.drop_columns`.

    Every re-run **overwrites** all three fields from the current matcher pass —
    no per-field merge, no preservation of hand-edits. Make manual edits after
    running auto-configure, not before.

    - `column_mapping` — `ColumnMapping` entries for matched columns where the
      source and target names differ.
    - `select_columns` — matched source column names when any source column is
      unmatched (fail-closed: unmatched source columns are excluded by absence).
      `None` when every source column matches.
    - `drop_columns` — target columns that the matcher couldn't pair with a
      source column. Reconcile resolves drop_columns names via column_mapping
      with a fallback to the name as-is, so target-only column names correctly
      skip the target side. `None` when every target column matches.
    """

    def __init__(self, strategy: IdentifierMatchingStrategy = NormalizedMatcher()) -> None:
        self._strategy = strategy

    def configure(self, table: Table, ctx: AutoConfigureContext) -> Table:
        source_names = [c.column_name for c in ctx.source_columns]
        target_names = [c.column_name for c in ctx.target_columns]

        name_mapping = self._strategy.match_all(source_names, target_names)

        mappings: list[ColumnMapping] = []
        matched_source: list[str] = []
        matched_target: set[str] = set()
        unmatched_source: list[str] = []
        for src_col in source_names:
            tgt_col = name_mapping[src_col]
            if tgt_col is None:
                unmatched_source.append(src_col)
                continue
            matched_source.append(src_col)
            matched_target.add(tgt_col)
            if src_col != tgt_col:
                mappings.append(ColumnMapping(source_name=src_col, target_name=tgt_col))

        unmatched_target = [t for t in target_names if t not in matched_target]

        if unmatched_source:
            logger.warning(
                f"Could not auto-match {len(unmatched_source)} source column(s) for "
                f"{table.source_name} -> {table.target_name}: {', '.join(unmatched_source)}. "
                f"Listed {len(matched_source)} matched column(s) in select_columns."
            )
        if unmatched_target:
            logger.warning(
                f"Target has {len(unmatched_target)} unmatched column(s) for "
                f"{table.source_name} -> {table.target_name}: {', '.join(unmatched_target)}. "
                f"Added to drop_columns."
            )

        return dataclasses.replace(
            table,
            column_mapping=mappings or None,
            select_columns=matched_source if unmatched_source else None,
            drop_columns=unmatched_target or None,
        )
