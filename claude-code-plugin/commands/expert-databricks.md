---
description: Expert reference for writing and debugging migrated Databricks notebooks, jobs and Delta tables
argument-hint: "[question]"
---

# Databricks Expert (migration focus)

Answer the user's question (`$ARGUMENTS`). Values like catalog, schemas, profile and jobs come
from `migration/migration.config.yml`. Confirm behaviour against the workspace with the Databricks
CLI (`--profile <databricks.profile>`) when possible.

## Deployment

- Deploy from git: a Git folder synced by CI, or a Databricks Asset Bundle (`databricks bundle deploy`).
  Jobs should reference the git-backed path. Stale copies under other `/Workspace` paths cause
  "fixed but still failing" confusion; check the job's task paths when a fix doesn't take effect.
- Edit notebooks in place and let git carry history. Never create `_v2` files.

## Serverless and access-mode gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `NOT_SUPPORTED_WITH_SERVERLESS` on `cache()`/`persist()`/`CACHE TABLE` | Not supported on serverless | Remove them; serverless manages memory |
| `DEFAULT` in `CREATE TABLE` fails | Column defaults need the `allowColumnDefaults` table feature | Set the value in the `INSERT`/`MERGE` instead, or enable the feature deliberately |
| `DBUtils(spark)` fails | Standard (shared) access mode | Use the notebook's global `dbutils` and pass it explicitly |
| `sys.path.append('/Workspace/...')` import fails | Depends on runtime and access mode | Package as a wheel in a UC Volume and `%pip install` it, or use `%run` |
| `CANNOT_DETERMINE_TYPE` in `createDataFrame` | A column is all `None` | Pass an explicit `StructType` schema |
| Notebook hangs after a large parallel JDBC load | Extra actions (`count()`, `display()`) after the load | Report from in-memory results and `dbutils.notebook.exit()` |

## Identifier and type pitfalls (common in migrations)

- **Mixed ID types.** Legacy systems often use INT keys where the new source uses UUID strings.
  Cast both sides of a join to `STRING`; implicit casts fail with `CAST_INVALID_INPUT`.
- **UUID case.** Different systems store UUIDs in different case. Normalize with `upper()` or `lower()`
  on both sides. A join that silently returns 0 rows is the typical symptom.
- **Empty-string casts.** `regexp_replace(...)` can yield `''`; guard with `when(col != '', col.cast(...))`.
- **Timestamps vs. decimal hours.** When a legacy column stored hours as a decimal and the new one is
  a `TIMESTAMP`, extract with `hour()`/`minute()` instead of arithmetic.
- **Time zones.** Legacy "local date" columns are usually derived in the business time zone.
  Reproduce the same conversion (`from_utc_timestamp`) before deriving dates, or you get off-by-one-day keys.

## Delta patterns

- Add a column to a large table: `ALTER TABLE ... ADD COLUMN`, backfill with `MERGE`, then verify
  `COUNT(col)`. Rebuild instead when changing types or many columns at once.
- Idempotent loads: `MERGE` on the business key from the legacy logic, not on a surrogate key.
- Deterministic deduplication: `ROW_NUMBER() OVER (PARTITION BY key ORDER BY <total order>)`. The
  `ORDER BY` must break every tie (add the id columns last), or results change run to run.
- Incremental watermarks: advance the watermark only after the write commits. Reset it when a
  table is rebuilt, or the rebuild silently skips history.

## Spatial

Use the native `GEOGRAPHY` type (SRID 4326) on recent runtimes. Load WKT via JDBC as text, then
`ST_GeogFromWKT`. Use `ST_Contains`, `ST_Distance` and H3 functions instead of custom UDFs.

## Useful CLI

```bash
databricks jobs list --profile <p> --output json
databricks jobs run-now <job_id> --profile <p>
databricks jobs get-run <run_id> --profile <p> --output json
databricks jobs get-run-output <task_run_id> --profile <p>
databricks api post /api/2.0/sql/statements --profile <p> --json '{"warehouse_id":"<databricks.warehouse_id>","statement":"..."}'
```
