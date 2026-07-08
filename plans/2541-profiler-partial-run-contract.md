# Plan: Profiler pipeline partial / best-effort run contract (#2541)

## Context

`assessments/pipeline.py` treats every step failure as fatal: `ddl`/`source_ddl` errors
abort mid-run, and any `sql`/`python` error re-raises at the end even though successful
tables are already written. The engine has **no notion of *why* a step failed**, so it
cannot tell *benign absence* (a view/feature legitimately missing for a deployment edition,
version, or permission) from a *real failure* (connectivity, auth, or a malformed query).

This forces the per-deployment "variant" workaround (Redshift, Teradata) and means "success"
today just means "every step passed" — the wrong definition for a diagnostic tool.

This plan implements the **engine failure-handling contract** that unblocks variant collapse
(#2540, #2542). It is intentionally scoped to the engine and is **fully backward compatible**:
the new tolerance is opt-in per step via `optional: true` (default `false`), so no existing
profiler changes behavior except that two failure tests become more precise.

### Design decisions (locked)

- **Opt-in mechanism:** per-step `optional: true` flag in the pipeline YAML. Purely
  category-driven tolerance was rejected — a typo'd column in a *required* step must still
  fail loudly so CI stays meaningful.
- **Classification signal:** SQLSTATE (ANSI, largely shared across Postgres/Redshift; usable
  for Teradata) with a thin per-driver numeric-code fallback for Teradata. No SQLAlchemy
  migration for Redshift — `redshift_connector` already exposes SQLSTATE under error key `'C'`.
- **`ddl` vs `source_ddl`:** `ddl` targets local DuckDB (our own schema) and stays strictly
  fatal. `source_ddl` targets the source DB and follows the same category policy as `sql`.
- **Success floor:** the run fails if *all* source `sql` steps come back `ABSENT` (guards
  against wrong DB/creds looking like a lean edition).

### Out of scope (tracked elsewhere)

- Teradata collapse (`core`/`pdcr`) — #2540. First natural consumer; see follow-up section.
- Redshift collapse + `variant` removal — #2542. See follow-up section.
- Downstream analysis/TCO inference of deployment type from extract data — #2540/#2542.

---

## Commit sequence

Each commit is self-contained, compiles, and passes tests on its own. Order matters:
1 → 2 → 3 → 4. Commit 5 is polish and can trail.

---

### Commit 1 — Introduce the error taxonomy (pure, no wiring)

**Goal:** add the vocabulary the engine will reason with, with zero behavior change.

**Files**
- `src/databricks/labs/lakebridge/assessments/errors.py` (new)
- `tests/unit/assessment/test_errors.py` (new)

**Action items**
- [ ] Define `ErrorCategory(str, Enum)` with: `CONNECTION`, `AUTH`, `ABSENCE`, `PERMISSION`,
      `SYNTAX`, `UNKNOWN`.
- [ ] Define `SourceQueryError(Exception)` carrying `category: ErrorCategory`,
      `sqlstate: str | None`, `reason: str`, and optional `step_name: str | None`.
- [ ] Implement `classify_sqlstate(sqlstate: str | None, message: str = "") -> ErrorCategory`:
  - `08*` → `CONNECTION`
  - `28*` → `AUTH`
  - `42P01`, `42703`, `42S02`, `3F000` → `ABSENCE`
  - `42501` → `PERMISSION`
  - `42601` → `SYNTAX`
  - Teradata numeric fallback parsed from `message` when SQLSTATE is missing/`None`:
    `[Error 3807]` → `ABSENCE`, `[Error 3523]` → `PERMISSION`.
  - anything unmapped → `UNKNOWN`.
- [ ] Unit tests: one case per category (both a Postgres/Redshift SQLSTATE and the Teradata
      numeric fallback for `ABSENCE`/`PERMISSION`), plus `None`/empty → `UNKNOWN`.

**Verify:** `pytest tests/unit/assessment/test_errors.py`

---

### Commit 2 — Centralize error extraction in the connector layer

**Goal:** stop erasing error semantics; every source query failure funnels into a typed
`SourceQueryError` with a populated `category`/`sqlstate`.

**Files**
- `src/databricks/labs/lakebridge/connections/database_manager.py`
- `src/databricks/labs/lakebridge/cli.py` (caller audit — `test_profiler_connection`)
- `tests/unit/connections/test_database_manager.py`

**Action items**
- [ ] Add `extract_sqlstate(exc: Exception) -> str | None` to the connector interface:
  - `_BaseConnector` (SQLAlchemy: Snowflake/MSSQL/Synapse/Oracle/Teradata): return
    `getattr(getattr(exc, "orig", None), "sqlstate", None)` or `pgcode` fallback.
  - `RedshiftConnector`: return `exc.args[0].get("C")` when `args[0]` is a dict, else `None`
    (confirmed: `redshift_connector` stores SQLSTATE under key `"C"`).
- [ ] Rewrite `DatabaseManager.fetch` to be the single choke point: catch broad driver
      exceptions, compute `sqlstate = self.connector.extract_sqlstate(e)`,
      `category = classify_sqlstate(sqlstate, str(e))`, and
      `raise SourceQueryError(category, sqlstate, reason=<concise first line>) from e`.
      Keep the existing first-line-only message trimming (teradatasql dumps stack + SQL).
- [ ] Audit callers that expect `ConnectionError` from `fetch`/`health_check`. Update
      `cli.test_profiler_connection` to also handle `SourceQueryError` (map `CONNECTION`/`AUTH`
      to the existing "connection validation failed" exit).
- [ ] Unit tests with fake driver exceptions per connector asserting the right `category`.

**Verify:** `pytest tests/unit/connections/test_database_manager.py tests/unit/test_cli_other.py`

---

### Commit 3 — Add `optional` step flag and `ABSENT` status (plumbing only)

**Goal:** introduce the opt-in surface and the new outcome, with no policy change yet
(behavior identical because nothing sets `optional: true`).

**Files**
- `src/databricks/labs/lakebridge/assessments/profiler_config.py`
- `src/databricks/labs/lakebridge/assessments/pipeline.py`
- `tests/unit/assessment/` (config tests)

**Action items**
- [ ] Add `optional: bool = False` to the `Step` dataclass. `load_config_from_yaml` already
      does `Step(**step)`, so YAML just needs the key — confirm no schema strictness blocks it.
- [ ] Add a validator (accept only bool) consistent with the other `_validate_*` methods.
- [ ] Add `StepExecutionStatus.ABSENT = "ABSENT"`.
- [ ] Unit test: a `Step` with `optional: true` round-trips from YAML; default is `False`.

**Verify:** `pytest tests/unit/assessment/`

---

### Commit 4 — Category-based execution policy in the engine

**Goal:** the engine acts on `category` + `optional` instead of blanket-fatal.

**Files**
- `src/databricks/labs/lakebridge/assessments/pipeline.py`
- `tests/integration/assessments/test_pipeline.py`
- `tests/resources/assessments/pipeline_config_sql_failure.yml` (+ referenced SQL)
- `tests/resources/assessments/` (new absence fixture)

**Action items**
- [ ] `_execute_sql_step` / `_execute_source_ddl_step`: stop wrapping into generic
      `RuntimeError`; let `SourceQueryError` propagate.
- [ ] `_process_step`: catch `SourceQueryError` and map to an outcome:
  - `CONNECTION` / `AUTH` → fatal-abort (any step type).
  - `ABSENCE` / `PERMISSION` on an `optional` step → `ABSENT`.
  - `ABSENCE` / `PERMISSION` on a required step → `ERROR`.
  - `SYNTAX` / `UNKNOWN` → `ERROR`.
- [ ] `execute()` rewrite:
  - Abort immediately (raise) on `CONNECTION`/`AUTH`.
  - Split the `ddl` vs `source_ddl` branch: `ddl` (local DuckDB) stays fatal on any error;
    `source_ddl` follows the category policy above.
  - End-of-run: fatal iff any `ERROR`; `ABSENT` is acceptable.
  - **Success floor:** if there is ≥1 source `sql` step and *every* source `sql` step is
    `ABSENT`, fail the run (likely wrong DB/creds).
  - Return a summary (counts of complete/absent/error) alongside the results list.
- [ ] **Rework the failure fixture:** `invalid_query.sql` is currently
      `SELECT * FROM non_existent_table;` (an *absence*, not a real error). Change it to a
      genuine syntax error (e.g. `SELCT 1`) so `test_run_sql_failure_pipeline` still asserts a
      real failure.
- [ ] **New absence fixture + test:** a pipeline with an `optional: true` step referencing a
      missing table → assert the step is `ABSENT` and the run completes (no raise).
- [ ] **New engine unit tests** with a fake executor raising `SourceQueryError` of each
      category: assert abort vs `ABSENT` vs `ERROR`, and assert the all-absent success floor.
- [ ] Confirm `test_run_python_failure_pipeline` still fatal (Python error → `UNKNOWN` → `ERROR`).

**Verify:** `pytest tests/integration/assessments/test_pipeline.py`
(plus the new unit tests; integration DB tests may need the sandbox fixtures)

---

### Commit 5 — Reporting + docs (polish, can trail)

**Goal:** partial-but-valid runs surface honestly to the user; no silent partials.

**Files**
- `src/databricks/labs/lakebridge/assessments/profiler.py`
- `docs/lakebridge/` (relevant profiler page, if any references failure behavior)

**Action items**
- [ ] `Profiler._execute`: ensure a run with `ABSENT` steps but no `ERROR` returns cleanly;
      log a summary ("completed; N metrics expected-absent for this deployment").
- [ ] Ensure real failures (`ERROR`, `CONNECTION`, `AUTH`) still raise with a clear message.
- [ ] Update any profiler doc/help text that describes "all steps must pass".

**Verify:** `pytest tests/unit/assessment/ tests/integration/assessments/`

---