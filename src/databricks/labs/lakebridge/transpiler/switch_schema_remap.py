"""
Post-transpile SQL remapping for Switch: qualified table namespaces and column names.

Config is CSV only. Uses sqlglot (Databricks dialect). Optional text fallback when parsing fails.
"""

from __future__ import annotations

import csv
import io
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlglot import exp, parse
from sqlglot.dialects.databricks import Databricks
from sqlglot.errors import ParseError

logger = logging.getLogger(__name__)

_DATABRICKS = Databricks


def _normalize_header(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _quote_databricks_ident(name: str) -> str:
    escaped = name.replace("`", "``")
    return f"`{escaped}`"


def session_prefix_sql(default_catalog: str, default_schema: str) -> str:
    """Return USE CATALOG / USE SCHEMA lines (Databricks), or empty string if both blank."""
    cat = default_catalog.strip()
    sch = default_schema.strip()
    lines: list[str] = []
    if cat:
        lines.append(f"USE CATALOG {_quote_databricks_ident(cat)};")
    if sch:
        lines.append(f"USE SCHEMA {_quote_databricks_ident(sch)};")
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def apply_session_prefix(sql_body: str, default_catalog: str, default_schema: str) -> str:
    prefix = session_prefix_sql(default_catalog, default_schema)
    return prefix + sql_body if prefix else sql_body


@dataclass
class RemapConfig:
    """Normalized remap configuration (built from CSV or constructed in tests)."""

    namespace_map: list[tuple[str, str]] = field(default_factory=list)
    column_map: dict[str, str] = field(default_factory=dict)
    column_map_by_table: dict[str, dict[str, str]] = field(default_factory=dict)
    sql_extensions: tuple[str, ...] = (".sql",)
    text_fallback_on_parse_error: bool = False


def load_remap_config_from_csv(
    *,
    namespace_csv: str | None = None,
    column_csv: str | None = None,
    text_fallback_on_parse_error: bool = False,
    sql_extensions: tuple[str, ...] | None = None,
) -> RemapConfig:
    """
    Load remap rules from two optional CSV bodies.

    Namespace CSV: headers include ``from_qualified``/``from`` and ``to_qualified``/``to``.
    Column CSV: ``qualified_table``, ``from_column``, ``to_column`` (case-insensitive headers).
    Use empty or ``*`` in ``qualified_table`` for global column renames.
    """
    pairs: list[tuple[str, str]] = []
    if namespace_csv and namespace_csv.strip():
        reader = csv.DictReader(io.StringIO(namespace_csv.strip()))
        if not reader.fieldnames:
            raise ValueError("Namespace remap CSV must have a header row.")
        for row in reader:
            norm = {_normalize_header(k): (v or "").strip() for k, v in row.items() if k}
            f = norm.get("from_qualified") or norm.get("from") or ""
            t = norm.get("to_qualified") or norm.get("to") or ""
            if f and t:
                pairs.append((f, t))
        pairs.sort(key=lambda x: len(x[0]), reverse=True)

    column_map: dict[str, str] = {}
    column_map_by_table: dict[str, dict[str, str]] = {}
    if column_csv and column_csv.strip():
        reader = csv.DictReader(io.StringIO(column_csv.strip()))
        if not reader.fieldnames:
            raise ValueError("Column remap CSV must have a header row.")
        for row in reader:
            norm = {_normalize_header(k): (v or "").strip() for k, v in row.items() if k}
            qt = norm.get("qualified_table", "")
            fc = norm.get("from_column", "")
            tc = norm.get("to_column", "")
            if not fc or not tc:
                continue
            if not qt or qt == "*":
                column_map[fc] = tc
            else:
                column_map_by_table.setdefault(qt, {})[fc] = tc

    ext = sql_extensions if sql_extensions is not None else (".sql",)
    return RemapConfig(
        namespace_map=pairs,
        column_map=column_map,
        column_map_by_table=column_map_by_table,
        sql_extensions=ext,
        text_fallback_on_parse_error=text_fallback_on_parse_error,
    )


def _qualified_table_name(table: exp.Table) -> str:
    parts: list[str] = []
    if table.catalog:
        parts.append(table.catalog)
    if table.db:
        parts.append(table.db)
    if table.name:
        parts.append(table.name)
    return ".".join(parts)


def _column_table_qualifier(column: exp.Column) -> str | None:
    parts: list[str] = []
    for p in (column.catalog, column.db, column.table):
        if p:
            parts.append(str(p))
    return ".".join(parts) if parts else None


def _parse_target_table(qualified: str) -> tuple[str | None, str | None, str]:
    parts = [p for p in qualified.split(".") if p]
    if len(parts) == 1:
        return None, None, parts[0]
    if len(parts) == 2:
        return None, parts[0], parts[1]
    return parts[0], parts[1], ".".join(parts[2:])


def _build_table_expression(catalog: str | None, db: str | None, name: str) -> exp.Table:
    kwargs: dict[str, Any] = {"this": exp.to_identifier(name)}
    if db:
        kwargs["db"] = exp.to_identifier(db)
    if catalog:
        kwargs["catalog"] = exp.to_identifier(catalog)
    return exp.Table(**kwargs)


def _namespace_dict(pairs: list[tuple[str, str]]) -> dict[str, str]:
    return dict(pairs)


def _remap_statement(statement: exp.Expression, cfg: RemapConfig, ns: dict[str, str]) -> exp.Expression:
    def pass_rename_columns(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column):
            return node
        qual = _column_table_qualifier(node)
        new_name: str | None = None
        if qual and qual in cfg.column_map_by_table:
            new_name = cfg.column_map_by_table[qual].get(node.name)
        if new_name is None and node.name in cfg.column_map:
            new_name = cfg.column_map[node.name]
        if new_name is None:
            return node
        new_args = dict(node.args)
        new_args["this"] = exp.to_identifier(new_name)
        return exp.Column(**new_args)

    s = statement.transform(pass_rename_columns)

    def pass_namespace_tables(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Table):
            q = _qualified_table_name(node)
            if q in ns:
                cat, db, name = _parse_target_table(ns[q])
                return _build_table_expression(cat, db, name)
        return node

    s = s.transform(pass_namespace_tables)

    def pass_namespace_columns(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column):
            return node
        qual = _column_table_qualifier(node)
        if qual and qual in ns:
            cat, db, tname = _parse_target_table(ns[qual])
            return exp.Column(
                this=node.this,
                table=exp.to_identifier(tname) if tname else None,
                db=exp.to_identifier(db) if db else None,
                catalog=exp.to_identifier(cat) if cat else None,
            )
        return node

    return s.transform(pass_namespace_columns)


def _text_fallback(sql: str, cfg: RemapConfig) -> str:
    out = sql
    for old, new in cfg.namespace_map:
        out = out.replace(old, new)
    return out


def remap_sql(sql: str, cfg: RemapConfig) -> tuple[str, bool]:
    """
    Rewrite SQL using sqlglot. Returns (new_sql, used_sqlglot).

    On parse failure: if text_fallback_on_parse_error, returns (fallback_sql, False);
    otherwise (original_sql, False).
    """
    ns = _namespace_dict(cfg.namespace_map)
    try:
        statements = parse(sql, dialect=_DATABRICKS)
    except ParseError as e:
        logger.warning("sqlglot parse failed: %s", e)
        if cfg.text_fallback_on_parse_error:
            return _text_fallback(sql, cfg), False
        return sql, False

    out: list[str] = []
    for stmt in statements:
        remapped = _remap_statement(stmt, cfg, ns)
        out.append(remapped.sql(dialect=_DATABRICKS, pretty=False))
    return ";\n".join(out), True


@dataclass
class RemapSummary:
    files_processed: int = 0
    files_changed: int = 0
    files_skipped: int = 0
    parse_failures: int = 0


class _DbutilsFs(Protocol):
    def ls(self, path: str) -> Any: ...

    def head(self, path: str, max_bytes: int | None = None) -> str: ...

    def put(self, path: str, contents: str, overwrite: bool = True) -> bool: ...


def _dbutils_collect_sql_paths(fs: _DbutilsFs, base: str, extensions: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    stack = [base]
    while stack:
        current = stack.pop()
        try:
            infos = fs.ls(current)
        except Exception as e:
            logger.warning("dbutils.fs.ls failed for %s: %s", current, e)
            continue
        for info in infos:
            path = getattr(info, "path", None) or (info.get("path") if isinstance(info, dict) else None)
            if not path:
                continue
            is_dir = getattr(info, "isDir", None)
            if is_dir is None and isinstance(info, dict):
                is_dir = info.get("isDir")
            if is_dir:
                stack.append(path)
            elif any(path.endswith(ext) for ext in extensions):
                out.append(path)
    return sorted(out)


def _clamp_max_workers(n: int) -> int:
    return max(1, min(n, 64))


def _coerce_max_workers(value: int | str) -> int:
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 1
        try:
            n = int(s)
        except ValueError:
            logger.warning("Invalid max_workers %r, using 1", value)
            return 1
    else:
        n = int(value)
    return _clamp_max_workers(n)


def _remap_single_dbutils_path(
    path: str,
    fs: Any,
    cfg: RemapConfig,
    default_catalog: str,
    default_schema: str,
) -> tuple[int, int, int]:
    """
    Remap one file. Returns ``(parse_failures_delta, files_changed_delta, files_skipped_delta)``.
    """
    try:
        raw = fs.head(path, max_bytes=1024 * 1024 * 50)
    except Exception as e:
        logger.warning("skip read %s: %s", path, e)
        return 0, 0, 1
    body = apply_session_prefix(raw, default_catalog, default_schema)
    new_sql, ok = remap_sql(body, cfg)
    parse_fail = 0 if ok else 1
    if new_sql == raw:
        return parse_fail, 0, 0
    try:
        fs.put(path, new_sql, True)
    except Exception as e:
        logger.warning("skip write %s: %s", path, e)
        return parse_fail, 0, 1
    return parse_fail, 1, 0


def remap_output_dir_dbutils(
    output_dir: str,
    cfg: RemapConfig,
    dbutils: Any,
    *,
    default_catalog: str = "",
    default_schema: str = "",
    max_workers: int | str = 1,
) -> RemapSummary:
    """
    Walk ``output_dir`` via ``dbutils.fs``, remap matching SQL files in place.

    ``dbutils`` must provide ``fs.ls``, ``fs.head``, ``fs.put`` like Databricks dbutils.
    With ``max_workers`` > 1, processes one path per thread (overlapping I/O).
    """
    summary = RemapSummary()
    fs = dbutils.fs
    paths = _dbutils_collect_sql_paths(fs, output_dir.rstrip("/"), cfg.sql_extensions)
    workers = _coerce_max_workers(max_workers)

    if workers <= 1:
        for path in paths:
            summary.files_processed += 1
            pf, ch, sk = _remap_single_dbutils_path(path, fs, cfg, default_catalog, default_schema)
            summary.parse_failures += pf
            summary.files_changed += ch
            summary.files_skipped += sk
        return summary

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_remap_single_dbutils_path, path, fs, cfg, default_catalog, default_schema)
            for path in paths
        ]
        for fut in as_completed(futures):
            summary.files_processed += 1
            try:
                pf, ch, sk = fut.result()
            except Exception as e:
                logger.warning("remap task failed: %s", e)
                summary.files_skipped += 1
                continue
            summary.parse_failures += pf
            summary.files_changed += ch
            summary.files_skipped += sk
    return summary


def iter_sql_files(root: Path, extensions: tuple[str, ...]) -> list[Path]:
    """List files under root matching extensions (for local / tests)."""
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and any(str(p).endswith(ext) for ext in extensions):
            out.append(p)
    return sorted(out)


def remap_tree_local(
    root: Path,
    cfg: RemapConfig,
    *,
    default_catalog: str = "",
    default_schema: str = "",
) -> RemapSummary:
    """Remap SQL files under a local directory (tests, CLI)."""
    summary = RemapSummary()
    for path in iter_sql_files(root, cfg.sql_extensions):
        summary.files_processed += 1
        raw = path.read_text(encoding="utf-8")
        body = apply_session_prefix(raw, default_catalog, default_schema)
        new_sql, ok = remap_sql(body, cfg)
        if not ok:
            summary.parse_failures += 1
        if new_sql == raw:
            continue
        path.write_text(new_sql, encoding="utf-8")
        summary.files_changed += 1
    return summary


def run(
    output_dir: str,
    namespace_remap_csv_path: str,
    column_remap_csv_path: str,
    default_catalog: str,
    default_schema: str,
    dbutils: Any,
    *,
    apply_schema_remap: str | bool = True,
    max_workers: int | str = 1,
) -> RemapSummary:
    """
    Entry point for the schema remapping notebook.

    ``apply_schema_remap`` defaults to enabled; set false to no-op (e.g. from a job parameter).
    Truthy strings: ``true``/``1``/``yes``/``on`` (case-insensitive).
    Requires at least one non-empty CSV path (namespace or column).
    ``max_workers`` bounds concurrent per-file work (clamped 1..64); >1 uses threads for I/O overlap.
    """
    if isinstance(apply_schema_remap, str):
        flag = apply_schema_remap.strip().lower() in ("1", "true", "yes", "on")
    else:
        flag = bool(apply_schema_remap)
    if not flag:
        logger.info("Schema remap skipped (disabled).")
        return RemapSummary()
    if not (output_dir or "").strip():
        logger.info("Schema remap skipped (empty output_dir).")
        return RemapSummary()
    ns_p = (namespace_remap_csv_path or "").strip()
    col_p = (column_remap_csv_path or "").strip()
    if not ns_p and not col_p:
        logger.info("Schema remap skipped (both CSV paths empty).")
        return RemapSummary()

    ns_csv = dbutils.fs.head(ns_p, max_bytes=1024 * 1024) if ns_p else ""
    col_csv = dbutils.fs.head(col_p, max_bytes=1024 * 1024) if col_p else ""
    cfg = load_remap_config_from_csv(namespace_csv=ns_csv or None, column_csv=col_csv or None)
    return remap_output_dir_dbutils(
        output_dir.strip(),
        cfg,
        dbutils,
        default_catalog=default_catalog or "",
        default_schema=default_schema or "",
        max_workers=max_workers,
    )
