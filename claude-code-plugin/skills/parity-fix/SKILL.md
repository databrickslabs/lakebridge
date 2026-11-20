---
name: parity-fix
description: Find and fix differences between a legacy table and its migrated Databricks table — schema first, then row counts per layer, then key-level differences, then root cause and fix. Use when a reconcile or validation shows a migrated table doesn't match the legacy system.
---

# Parity fix: schema first, data second

## Input

`TABLE`: the migrated target table, or the legacy table name. Resolve the other side with
`target.mapping_file` or by asking.

## Safety rule

Never load rows from the legacy target table into the new table to make counts match unless
`rules.allow_legacy_data_copy` is true **and** the user approves in this session. If that is the only
way to close the gap, stop and explain why the data can't come from the upstream sources.

## Phase 0 — Read the blueprint (blocking)

Before running any query:
1. Read the full legacy logic that populates the legacy table (procedure, view, ETL mapping).
2. Write down its merge/unique key, source tables, every join with type and condition, every
   filter, deduplication and its ordering, calculated columns, and post-load updates.
3. Read the current Databricks code for the table and list every place it differs from (2).

Many parity bugs are visible here, before any query.

## Phase 1 — Schema (blocking)

Compare columns and types. Prefer `databricks labs lakebridge reconcile` with `report_type: schema`.
Otherwise query `INFORMATION_SCHEMA.COLUMNS` on both sides — on the legacy side through
`source.query_command` from the config; if that is empty, ask the user for the legacy schema.

Classify each difference: missing column, extra column, incompatible type, compatible cast, or name
casing. Fix schema problems before looking at data. Data comparisons on a wrong schema are noise.

## Phase 2 — Counts per layer

Count rows for the same time window at each layer: legacy source → bronze/raw → silver →
target, and the legacy table. Where the difference first appears is where the bug is.

| Layer | Table | Rows | Δ vs legacy |
|---|---|---|---|

## Phase 3 — Key-level difference

Use reconcile `report_type: data` (or `row` if there is no key) with `join_columns` set to the legacy
key, or run anti-joins on the key both ways. Take samples of `missing_in_tgt`, `missing_in_src` and
`mismatch_data`, and look at 5–10 rows of each in both systems.

## Phase 4 — Root cause

Common causes, in rough order of frequency:

| Symptom | Likely cause |
|---|---|
| Join returns 0 rows or drops most rows | ID type mismatch (INT vs UUID string) or UUID case differences |
| Counts differ by a small, changing amount run to run | Non-deterministic deduplication (`ROW_NUMBER` without a total order) |
| Rows shifted by one day | Local-date derived in UTC instead of the business time zone |
| Target has fewer rows only for older periods | Legacy table has history that predates the new source; incremental watermark skipped history after a rebuild |
| Target has more rows | Missing filter or soft-delete condition from the legacy logic; `LEFT` vs `INNER` join |
| Values differ only in some columns | Calculated-column expression, NULL semantics, integer division, rounding |
| Legacy maps IDs through a lookup table | The new code joins on the natural key instead of the mapped legacy ID |

Confirm the cause with a query that shows it directly before changing code.

## Phase 5 — Fix

Fix at the layer where the difference appears. Edit in place. Redeploy and rerun the affected task.

Accepted differences (for example, new rows in the new source the legacy system never received)
are documented in the report with evidence. Don't hide them with reconcile transformations.

## Phase 6 — Verify

Rerun Phases 1–3. Done means: schema matches, counts match or the difference is fully explained,
and key-level mismatches are zero or accepted with evidence.

## Report

```
<table>: FIXED | ACCEPTED_DIFFERENCES | NEEDS_USER
Root cause: <one sentence, with the evidence query>
Change:     <files / commit>
Before → after: rows <a> → <b> (legacy <c>), key mismatches <x> → <y>
Accepted differences: <list with evidence, if any>
```
