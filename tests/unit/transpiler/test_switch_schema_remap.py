from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from databricks.labs.lakebridge.transpiler.switch_schema_remap import (
    RemapConfig,
    RemapSummary,
    apply_session_prefix,
    load_remap_config_from_csv,
    remap_output_dir_dbutils,
    remap_sql,
    remap_tree_local,
    run,
    session_prefix_sql,
)


def test_load_namespace_csv_from_to_headers():
    raw = "from,to\na.b.t,c.d.t\n"
    cfg = load_remap_config_from_csv(namespace_csv=raw, column_csv=None)
    assert cfg.namespace_map == [("a.b.t", "c.d.t")]


def test_load_namespace_csv_qualified_headers():
    raw = "from_qualified,to_qualified\nlegacy.sch.t1,uc.sch.t1\n"
    cfg = load_remap_config_from_csv(namespace_csv=raw, column_csv=None)
    assert cfg.namespace_map == [("legacy.sch.t1", "uc.sch.t1")]


def test_load_column_csv_global_and_scoped():
    raw = (
        "qualified_table,from_column,to_column\n"
        "*,old_g,new_g\n"
        "sch.t,c1,c1r\n"
    )
    cfg = load_remap_config_from_csv(namespace_csv=None, column_csv=raw)
    assert cfg.column_map == {"old_g": "new_g"}
    assert cfg.column_map_by_table == {"sch.t": {"c1": "c1r"}}


def test_session_prefix_sql_order():
    assert "USE CATALOG" in session_prefix_sql("mycat", "")
    assert "USE SCHEMA" in session_prefix_sql("", "mysch")
    s = session_prefix_sql("c", "s")
    assert s.index("USE CATALOG") < s.index("USE SCHEMA")


def test_apply_session_prefix():
    body = "SELECT 1"
    out = apply_session_prefix(body, "cat1", "sch1")
    assert out.startswith("USE CATALOG")
    assert "USE SCHEMA" in out
    assert body in out


def test_remap_sql_namespace_and_column():
    cfg = RemapConfig(
        namespace_map=[("legacy.sch.t1", "uc.sch.t1")],
        column_map={"old_c": "new_c"},
    )
    sql = "SELECT old_c FROM legacy.sch.t1"
    out, ok = remap_sql(sql, cfg)
    assert ok
    assert "uc.sch.t1" in out
    assert "new_c" in out


def test_remap_sql_with_default_catalog_schema_prefix():
    cfg = RemapConfig(namespace_map=[], column_map={})
    raw = "SELECT 1"
    body = apply_session_prefix(raw, "main", "dbo")
    out, ok = remap_sql(body, cfg)
    assert ok
    assert "USE CATALOG" in out
    assert "USE SCHEMA" in out


def test_remap_sql_column_map_by_table():
    cfg = RemapConfig(
        namespace_map=[("sch.t", "uc.sch.t")],
        column_map_by_table={"sch.t": {"c1": "renamed_c1"}},
    )
    sql = "SELECT sch.t.c1 FROM sch.t"
    out, ok = remap_sql(sql, cfg)
    assert ok
    assert "renamed_c1" in out


def test_remap_sql_parse_failure_no_fallback():
    cfg = RemapConfig(namespace_map=[], text_fallback_on_parse_error=False)
    bad = "this is not sql {{{"
    out, ok = remap_sql(bad, cfg)
    assert not ok
    assert out == bad


def test_remap_sql_parse_failure_with_text_fallback():
    cfg = RemapConfig(
        namespace_map=[("OLD", "NEW")],
        text_fallback_on_parse_error=True,
    )
    bad = "not sql OLD {{{"
    out, ok = remap_sql(bad, cfg)
    assert not ok
    assert "NEW" in out


def test_remap_tree_local(tmp_path: Path):
    d = tmp_path / "sub"
    d.mkdir()
    (d / "a.sql").write_text("SELECT z FROM db.t", encoding="utf-8")
    cfg = RemapConfig(
        namespace_map=[("db.t", "uc.sch.t")],
        column_map={"z": "zz"},
    )
    summary = remap_tree_local(tmp_path, cfg)
    assert summary.files_processed == 1
    assert summary.files_changed == 1
    text = (d / "a.sql").read_text(encoding="utf-8")
    assert "uc.sch.t" in text
    assert "zz" in text


def test_remap_tree_local_with_prefix(tmp_path: Path):
    d = tmp_path / "sub"
    d.mkdir()
    (d / "a.sql").write_text("SELECT 1", encoding="utf-8")
    cfg = RemapConfig()
    summary = remap_tree_local(tmp_path, cfg, default_catalog="c", default_schema="s")
    assert summary.files_changed == 1
    text = (d / "a.sql").read_text(encoding="utf-8")
    assert "USE CATALOG" in text


def test_remap_output_dir_dbutils():
    files = {
        "/out/x.sql": "SELECT a FROM s.t",
        "/out/nested/y.sql": "SELECT 1",
    }

    class MockFs:
        def ls(self, path: str):
            if path == "/out":
                return [
                    SimpleNamespace(path="/out/x.sql", isDir=False),
                    SimpleNamespace(path="/out/nested", isDir=True),
                ]
            if path == "/out/nested":
                return [SimpleNamespace(path="/out/nested/y.sql", isDir=False)]
            return []

        def head(self, path: str, max_bytes: int | None = None):
            return files[path]

        def put(self, path: str, contents: str, overwrite: bool = True):
            files[path] = contents
            return True

    dbutils = SimpleNamespace(fs=MockFs())
    cfg = RemapConfig(namespace_map=[("s.t", "c.s.t")])
    summary = remap_output_dir_dbutils("/out", cfg, dbutils)
    assert summary.files_processed == 2
    assert summary.files_changed >= 1
    assert "c.s.t" in files["/out/x.sql"]


def test_remap_output_dir_parallel_matches_sequential():
    start = {
        "/out/a.sql": "SELECT 1 FROM s.t",
        "/out/b.sql": "SELECT 2 FROM s.t",
        "/out/c.sql": "SELECT 3 FROM s.t",
    }
    files_seq = dict(start)
    files_par = dict(start)
    cfg = RemapConfig(namespace_map=[("s.t", "u.v.w")])

    def make_dbutils(files_dict):
        class MockFs:
            def ls(self, path: str):
                if path == "/out":
                    return [
                        SimpleNamespace(path="/out/a.sql", isDir=False),
                        SimpleNamespace(path="/out/b.sql", isDir=False),
                        SimpleNamespace(path="/out/c.sql", isDir=False),
                    ]
                return []

            def head(self, path: str, max_bytes: int | None = None):
                return files_dict[path]

            def put(self, path: str, contents: str, overwrite: bool = True):
                files_dict[path] = contents
                return True

        return SimpleNamespace(fs=MockFs())

    sum_seq = remap_output_dir_dbutils("/out", cfg, make_dbutils(files_seq), max_workers=1)
    sum_par = remap_output_dir_dbutils("/out", cfg, make_dbutils(files_par), max_workers=4)
    assert sum_seq == sum_par
    assert files_seq == files_par
    for body in files_seq.values():
        assert "u.v.w" in body


def test_remap_max_workers_clamped_in_run():
    files = {"/out/a.sql": "SELECT 1 FROM x.y"}
    ns_csv = "from,to\nx.y,z.a\n"

    class MockFs:
        def ls(self, path: str):
            if path == "/out":
                return [SimpleNamespace(path="/out/a.sql", isDir=False)]
            return []

        def head(self, path: str, max_bytes: int | None = None):
            if path == "/vol/ns.csv":
                return ns_csv
            return files[path]

        def put(self, path: str, contents: str, overwrite: bool = True):
            files[path] = contents
            return True

    dbutils = SimpleNamespace(fs=MockFs())
    summary = run(
        "/out",
        "/vol/ns.csv",
        "",
        "",
        "",
        dbutils,
        max_workers="9999",
    )
    assert isinstance(summary, RemapSummary)
    assert summary.files_changed == 1


@pytest.mark.parametrize(
    "flag",
    ["false", "0", "", "no"],
)
def test_run_skipped_when_disabled(flag: str):
    class Fs:
        def head(self, *a, **k):
            raise AssertionError("should not read config")

    dbutils = SimpleNamespace(fs=Fs())
    summary = run("/out", "/ns.csv", "/col.csv", "", "", dbutils, apply_schema_remap=flag)
    assert summary.files_processed == 0


def test_run_skipped_when_both_csv_empty():
    class Fs:
        def head(self, *a, **k):
            raise AssertionError("should not read config")

    dbutils = SimpleNamespace(fs=Fs())
    summary = run("/out", "", " ", "", "", dbutils, apply_schema_remap=True)
    assert summary.files_processed == 0


def test_run_skipped_when_empty_output_dir():
    class Fs:
        def head(self, *a, **k):
            raise AssertionError("should not read config")

    dbutils = SimpleNamespace(fs=Fs())
    summary = run("  ", "/ns.csv", "", "", "", dbutils, apply_schema_remap=True)
    assert summary.files_processed == 0


def test_run_defaults_apply_schema_remap_enabled():
    files = {"/out/a.sql": "SELECT x FROM t.old"}
    ns_csv = "from,to\nt.old,u.v.w\n"

    class MockFs:
        def ls(self, path: str):
            if path == "/out":
                return [SimpleNamespace(path="/out/a.sql", isDir=False)]
            return []

        def head(self, path: str, max_bytes: int | None = None):
            if path == "/vol/ns.csv":
                return ns_csv
            return files[path]

        def put(self, path: str, contents: str, overwrite: bool = True):
            files[path] = contents
            return True

    dbutils = SimpleNamespace(fs=MockFs())
    summary = run("/out", "/vol/ns.csv", "", "", "", dbutils)
    assert summary.files_changed == 1


def test_run_loads_csv_and_processes():
    files = {"/out/a.sql": "SELECT x FROM t.old"}
    ns_csv = "from,to\nt.old,u.v.w\n"

    class MockFs:
        def ls(self, path: str):
            if path == "/out":
                return [SimpleNamespace(path="/out/a.sql", isDir=False)]
            return []

        def head(self, path: str, max_bytes: int | None = None):
            if path == "/vol/ns.csv":
                return ns_csv
            return files[path]

        def put(self, path: str, contents: str, overwrite: bool = True):
            files[path] = contents
            return True

    dbutils = SimpleNamespace(fs=MockFs())
    summary = run("/out", "/vol/ns.csv", "", "cat", "sch", dbutils, apply_schema_remap=True)
    assert summary.files_processed == 1
    assert summary.files_changed == 1
    assert "USE CATALOG" in files["/out/a.sql"]
    assert "u.v.w" in files["/out/a.sql"]
