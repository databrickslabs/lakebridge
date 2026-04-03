---
draft: true
title: Terminology Guide
---

# Lakebridge Terminology Guide

This page establishes the canonical terms used across all Lakebridge documentation. When writing or editing docs, use the terms in the **Use this** column and avoid the variants in the **Not these** column.

## Terms

| Concept | Use this | Not these |
|---|---|---|
| Converting SQL or ETL code to Databricks format | **Transpile** / **Transpilation** | Convert, Transform, Migration (for the code-conversion step specifically) |
| Microsoft SQL Server | **SQL Server** | MSSQL, mssql (except in code/dialect keys), MS SQL Server, Microsoft SQL |
| Azure Synapse Analytics | **Azure Synapse Analytics** | Synapse, Azure Synapse (use the full name on first mention; abbreviations are acceptable in tables/code) |
| Validating data after migration | **Reconcile** / **Reconciliation** | Validation, Comparison |
| The system being migrated from | **Source system** | Legacy environment, Existing warehouse, Old system |
| IBM DataStage | **DataStage** | datastage (except in dialect keys and code) |
| The output target | **Databricks** / **Databricks SQL** | Target, DBSQL (except in technical tables) |

## Code Block Languages

Use consistent language identifiers in fenced code blocks:

| Content | Language tag to use |
|---|---|
| CLI commands | `bash` |
| Shell scripts | `bash` |
| YAML config files | `yaml` |
| JSON config files | `json` |
| Python code | `python` |
| SQL | `sql` |
| Console output / expected output | `console` |

**Avoid:** `shell`, `sh`, `commandline`, `cmd` — use `bash` instead.

## Dialect Keys

When referring to dialect identifiers used in CLI flags or config files, use lowercase code format: `mssql`, `snowflake`, `synapse`, `oracle`, `teradata`, `netezza`, `redshift`, `datastage`, `ssis`.

These are technical identifiers and do not follow the canonical naming above.
