"""Static checks that the SQL Server profiler queries follow the rules that keep them portable.

Each rule here comes from a bug reproduced on real SQL Server instances; these tests guard the rule,
not the full behaviour (that needs a live server):

* case-sensitive collations match system schema/object names and our own aliases exactly as written;
* multi_db UNIONs catalog text from databases that may use different collations;
* multi_db splices database names into dynamic SQL as literals, which must stay Unicode;
* sys_info must compile on SQL Server versions that lack columns added later.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ASSESSMENTS = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments"
_MSSQL = _ASSESSMENTS / "mssql"
_MULTI_DB = _MSSQL / "multi_db"

# Every SQL Server-family query file: mssql (single_db + multi_db) and legacy_synapse. DDL files are excluded.
_QUERY_FILES = sorted(
    p for d in (_MSSQL, _ASSESSMENTS / "legacy_synapse") for p in d.rglob("*.sql") if not p.name.endswith("_ddl.sql")
)
_STATIC_MSSQL_QUERIES = sorted(p for p in _MSSQL.glob("*.sql") if not p.name.endswith("_ddl.sql"))
_MULTI_DB_QUERIES = sorted(_MULTI_DB.glob("*.sql"))


def _rel(path: Path) -> str:
    return str(path.relative_to(_ASSESSMENTS))


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def _synapse_query_strings() -> list[str]:
    """SQL returned by the Synapse query builders (docstrings excluded)."""
    tree = ast.parse((_ASSESSMENTS / "synapse/common/queries.py").read_text())
    queries = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, (ast.Constant, ast.JoinedStr)):
            parts = [node.value] if isinstance(node.value, ast.Constant) else node.value.values
            queries.append("".join(p.value for p in parts if isinstance(p, ast.Constant) and isinstance(p.value, str)))
    return queries


def _ddl_columns(ddl_file: Path) -> dict[str, exp.DataType]:
    """Column name (upper-cased) -> DuckDB type, from a CREATE TABLE statement."""
    create = sqlglot.parse_one(ddl_file.read_text(), read="duckdb")
    assert isinstance(create, exp.Create)
    schema = create.this
    assert isinstance(schema, exp.Schema)
    return {col.name.upper(): col.args["kind"] for col in schema.expressions if isinstance(col, exp.ColumnDef)}


_SYSTEM_REF = re.compile(r"\b(information_schema|sys)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def _system_ref_violations(sql: str) -> list[str]:
    # Under a case-sensitive collation SQL Server matches system schema and object names exactly:
    # INFORMATION_SCHEMA views are defined in upper case, sys.* catalog views and DMVs in lower case.
    violations = []
    for schema, obj in _SYSTEM_REF.findall(_strip_comments(sql)):
        if schema.lower() == "information_schema":
            matches = schema == "INFORMATION_SCHEMA" and obj == obj.upper()
        else:
            matches = schema == "sys" and obj == obj.lower()
        if not matches:
            violations.append(f"{schema}.{obj}")
    return violations


@pytest.mark.parametrize("query_file", _QUERY_FILES, ids=_rel)
def test_system_schema_and_object_names_use_their_defined_case(query_file: Path) -> None:
    assert not _system_ref_violations(query_file.read_text())


def test_synapse_queries_use_defined_case_for_system_names() -> None:
    violations = [v for q in _synapse_query_strings() for v in _system_ref_violations(q)]
    assert not violations


def _alias_case_mismatches(sql: str) -> list[str]:
    """References to our own CTE/alias names whose case differs from the declaration."""
    tree = sqlglot.parse_one(sql, read="tsql")
    declared = {a.alias for a in tree.find_all(exp.Alias) if a.alias}
    declared |= {cte.alias for cte in tree.find_all(exp.CTE) if cte.alias}
    by_lower: dict[str, set[str]] = {}
    for name in declared:
        by_lower.setdefault(name.lower(), set()).add(name)
    mismatches = []
    for col in tree.find_all(exp.Column):
        spellings = by_lower.get(col.name.lower())
        if spellings and col.name not in spellings:
            mismatches.append(f"{col.name} (declared as {', '.join(sorted(spellings))})")
    return mismatches


@pytest.mark.parametrize("query_file", _STATIC_MSSQL_QUERIES, ids=_rel)
def test_aliases_are_referenced_in_their_declared_case(query_file: Path) -> None:
    # Under a case-sensitive collation the columns of our own CTEs and aliases are case-sensitive too.
    assert not _alias_case_mismatches(query_file.read_text())


def _cols_items(sql: str) -> list[str]:
    """Select-list items of a multi_db query's shared @cols declaration."""
    decl = sql[sql.index("DECLARE @cols") : sql.index(";", sql.index("DECLARE @cols"))]
    content = "".join(re.findall(r"N'((?:[^']|'')*)'", decl))
    return [item.strip() for item in content.split(",") if item.strip()]


@pytest.mark.parametrize("query_file", [p for p in _MULTI_DB_QUERIES if "DECLARE @cols" in p.read_text()], ids=_rel)
def test_multi_db_text_columns_are_collated_to_the_database_default(query_file: Path) -> None:
    # Databases on one instance can use different collations; UNION ALL of their text columns
    # fails unless every branch collates text to one collation. COLLATE is invalid on non-text types.
    ddl = _ddl_columns(_MSSQL / f"{query_file.stem}_ddl.sql")
    problems = []
    for item in _cols_items(query_file.read_text()):
        name = item.split()[-1].upper()
        is_text = ddl[name].is_type(*exp.DataType.TEXT_TYPES)
        has_collate = "COLLATE DATABASE_DEFAULT" in item.upper()
        if is_text != has_collate:
            problems.append(item)
        # A text literal (e.g. '[REDACTED]') must be Unicode like the catalog text it is UNIONed with.
        if "''" in item and not item.startswith("N''"):
            problems.append(item)
    assert not problems


def test_multi_db_indexed_views_collates_text_columns() -> None:
    sql = (_MULTI_DB / "indexed_views.sql").read_text()
    text_refs = re.findall(r"\b\w\.\[(?:name|type_desc)\](\s+COLLATE DATABASE_DEFAULT)?", sql)
    assert text_refs, "expected the view/schema/index name columns in the query"
    assert all(text_refs), "every name/type_desc column needs COLLATE DATABASE_DEFAULT"
    assert not re.search(r"\[(?:index_id|object_id|schema_id)\]\s+COLLATE", sql), "COLLATE is invalid on int columns"


@pytest.mark.parametrize("query_file", _MULTI_DB_QUERIES, ids=_rel)
def test_multi_db_database_name_literals_are_unicode(query_file: Path) -> None:
    # A plain '...' literal is varchar in the server code page, so non-ASCII database names would turn into '?'.
    sql = query_file.read_text()
    spliced = sql.count("QUOTENAME([name], '''')")
    unicode_spliced = len(re.findall(r"N'\s*\+\s*QUOTENAME\(\[name\], ''''\)", sql))
    assert spliced == unicode_spliced


def test_sys_info_defaults_every_version_dependent_column() -> None:
    sql = _strip_comments((_MSSQL / "sys_info.sql").read_text())
    defaults = re.findall(r"CAST\(NULL AS [^)]+\)\s+AS\s+(\w+)", sql)
    inner_select = sql[sql.index("CROSS APPLY") :]
    inner_cols = re.findall(r"\b(\w+)\b(?=\s*,|\s*FROM)", inner_select[inner_select.index("SELECT") + 6 :])
    outer_cols = re.findall(r"\bs\.(\w+)", sql)

    assert defaults, "expected NULL defaults for columns added in later SQL Server versions"
    # Each default only takes effect if the inner query references the column unqualified.
    assert set(defaults) <= set(inner_cols)
    assert outer_cols == inner_cols
    # The DMV columns plus extract_ts, in DDL order.
    assert [c.upper() for c in outer_cols] + ["EXTRACT_TS"] == list(_ddl_columns(_MSSQL / "sys_info_ddl.sql"))
