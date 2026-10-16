---
description: Expert reference for the Lakebridge CLI — analyze, transpile, llm-transpile, profiler and reconcile
argument-hint: "[question]"
---

# Lakebridge Expert

Answer the user's question (`$ARGUMENTS`) using this reference, the Lakebridge documentation
(https://databrickslabs.github.io/lakebridge/) and, when available, `databricks labs lakebridge <command> --help`.
Prefer running a read-only command to confirm behaviour over guessing.

## Where each command fits

```
Assess                         Convert                          Validate
------                         -------                          --------
execute-database-profiler      install-transpile                configure-reconcile
analyze                        transpile / llm-transpile        reconcile / aggregates-reconcile
```

## Assessment

| Command | Use it to |
|---|---|
| `configure-database-profiler`, `execute-database-profiler --source-tech <tech>` | Profile workload and usage on the source system into a DuckDB extract |
| `analyze --source-directory <dir> --report-file <file> --source-tech <tech>` | Inventory and complexity report of SQL/ETL code. Use it to size and order the migration queue. |

Split monolithic SQL files into one object per file first (SQL Splitter) so analysis and
conversion work per object.

## Conversion

| Command | Notes |
|---|---|
| `install-transpile` | Installs the transpilers (BladeBridge, Morpheus; `--include-llm-transpiler true` for Switch) |
| `describe-transpile` | Shows installed transpilers and their supported dialects |
| `transpile --input-source <dir> --output-folder <dir> --source-dialect <d> [--skip-validation false --catalog-name c --schema-name s] [--error-file-path f]` | Deterministic conversion. Validation compiles the output against the given catalog/schema. |
| `llm-transpile --input-source <dir> --output-ws-folder /Workspace/... --source-dialect <d> --catalog-name c --schema-name s --volume v --foundation-model <endpoint> --accept-terms true` | Switch: LLM-based conversion that runs as a Databricks job. Good for procedural code and dialects the deterministic transpilers don't cover. |

Reading transpile results:
- Check the error file first. Parsing errors mean the input needs splitting or cleanup; generation
  errors mean the construct is unsupported and needs manual or LLM conversion.
- Transpiled output is a starting point for procedural code (cursors, temp tables, dynamic SQL,
  multi-statement MERGE logic). Route those objects through the `migrate-object` skill.

## Reconciliation

Set up with `configure-reconcile` (creates the reconcile job and metadata tables), then run
`reconcile` or `aggregates-reconcile`. `auto-configure-recon-tables` can discover tables to add.

| `report_type` | Compares | Key outputs |
|---|---|---|
| `schema` | Column names and types | `schema_comparison`, `schema_difference` |
| `row` | Row hashes, no join key needed | `missing_in_src`, `missing_in_tgt` |
| `data` | Row and column values via `join_columns` | `mismatch_data`, `missing_in_src`, `missing_in_tgt`, `mismatch_columns` |
| `all` | `data` + `schema` | all of the above |

Useful table-level options: `join_columns`, `select_columns`, `drop_columns`, `column_mapping`,
`transformations` (normalize values before comparing — trimming, casing, timezone), `column_thresholds`,
`table_thresholds`, `filters`, `jdbc_reader_options`, `aggregates`.

Tips:
- Start with `schema`, then `row` or `data`. Schema problems make data comparisons noisy.
- Use `transformations` for known, accepted representation differences, and document each one.
  Don't use them to hide real logic bugs.
- Use `filters` to compare the same time window on both sides when the source keeps receiving data.
