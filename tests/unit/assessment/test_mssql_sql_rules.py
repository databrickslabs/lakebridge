"""Quick static guards for rules the SQL Server profiler queries must follow.

Each rule comes from a bug reproduced on real SQL Server; running the queries (integration tests) is
what proves them, these only catch an obvious regression early, without a server.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_ASSESSMENTS = Path(__file__).resolve().parents[3] / "src/databricks/labs/lakebridge/resources/assessments"
_MSSQL = _ASSESSMENTS / "mssql"
_QUERIES = sorted(
    p
    for folder in (_MSSQL, _ASSESSMENTS / "legacy_synapse")
    for p in folder.rglob("*.sql")
    if not p.name.endswith("_ddl.sql")
)
_MULTI_DB = sorted((_MSSQL / "multi_db").glob("*.sql"))


def _name(path: Path) -> str:
    return str(path.relative_to(_ASSESSMENTS))


def _without_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL))


def _wrong_case_system_names(sql: str) -> list[str]:
    # Case-sensitive collations match system schema/object names exactly as written:
    # INFORMATION_SCHEMA views are defined in upper case, sys.* views and DMVs in lower case.
    wrong = []
    for schema, name in re.findall(r"\b(information_schema|sys)\.(\w+)", _without_comments(sql), re.IGNORECASE):
        expected = (
            ("INFORMATION_SCHEMA", name.upper()) if schema.lower() == "information_schema" else ("sys", name.lower())
        )
        if (schema, name) != expected:
            wrong.append(f"{schema}.{name}")
    return wrong


@pytest.mark.parametrize("query", _QUERIES, ids=_name)
def test_system_names_use_their_defined_case(query: Path) -> None:
    assert not _wrong_case_system_names(query.read_text(encoding="utf-8"))


def test_synapse_system_names_use_their_defined_case() -> None:
    tree = ast.parse((_ASSESSMENTS / "synapse/common/queries.py").read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    strings = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]
    assert not [name for text in strings for name in _wrong_case_system_names(text)]


@pytest.mark.parametrize("query", _MULTI_DB, ids=_name)
def test_multi_db_database_names_are_unicode_literals(query: Path) -> None:
    # A plain '...' literal is varchar in the server's code page, so non-ASCII database names would become '?'.
    sql = query.read_text(encoding="utf-8")
    assert sql.count("QUOTENAME([name], '''')") == sql.count("N' + QUOTENAME([name], '''')")


@pytest.mark.parametrize("query", [q for q in _MULTI_DB if "UNION ALL" in q.read_text(encoding="utf-8")], ids=_name)
def test_multi_db_unions_collate_text(query: Path) -> None:
    # Databases on one server can use different collations; UNION ALL of their text fails without COLLATE.
    assert "COLLATE DATABASE_DEFAULT" in query.read_text(encoding="utf-8")


# sys.dm_os_sys_info columns added in SQL Server 2016 or later (Microsoft Learn), absent on older versions.
_VERSION_DEPENDENT_SYS_INFO_COLUMNS = [
    "softnuma_configuration",
    "softnuma_configuration_desc",
    "sql_memory_model",
    "sql_memory_model_desc",
    "socket_count",
    "cores_per_socket",
    "numa_node_count",
    "process_physical_affinity",
    "container_type",
    "container_type_desc",
]


@pytest.mark.parametrize("column", _VERSION_DEPENDENT_SYS_INFO_COLUMNS)
def test_sys_info_defaults_version_dependent_columns(column: str) -> None:
    # Without a NULL default, the whole sys_info query fails to compile on versions that lack the column.
    sql = (_MSSQL / "sys_info.sql").read_text(encoding="utf-8")
    assert re.search(rf"CAST\(NULL AS [\w() ]+\)\s+AS\s+{column}\b", sql)
