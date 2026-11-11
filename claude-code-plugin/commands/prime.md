---
description: Load migration context (config, queue, recent reviews) before working on a Lakebridge migration
---

# Prime: Lakebridge migration context

## Read

1. `migration/migration.config.yml` (or the path the user gives). If it is missing, stop and
   suggest `/lakebridge:init`.
2. The queue at `paths.queue`. Summarize counts per status.
3. The three most recent files in `paths.reviews` and `paths.fixes`, if any.
4. `git log --oneline -15` and `git status --short`.

## Check (read-only)

- `databricks auth describe --profile <databricks.profile>` authenticates.
- `databricks labs lakebridge describe-transpile` lists an installed transpiler for
  `project.source_dialect`.
- The catalog and schemas in the config exist: `databricks schemas list <catalog> --profile <p>`.

Report failures as a short checklist; do not try to fix credentials yourself.

## Rules that apply to every task in this migration

1. **The legacy logic is the blueprint.** Before writing or changing target code for an object,
   read the full legacy definition and extract its keys, joins, filters, deduplication and
   calculated columns. Do not invent join strategies.
2. **Schema first, data second.** Validate columns and types before comparing row counts.
3. **Localize before fixing.** Count rows at each layer (source → bronze → silver → gold) to find
   where a difference appears. Fix it at that layer.
4. **No legacy data copy without approval.** Loading rows from the legacy target into the new
   target hides bugs. It requires explicit user approval (`rules.allow_legacy_data_copy`).
5. **Edit in place.** Fix the existing file; never create `_v2` copies. Git is the history.
6. **Verify on the platform.** A change is done when the job or query ran on Databricks and the
   validation passed, not when the code looks right.

## Report

A short summary: project, source dialect, target catalog, queue progress, last review verdicts,
and anything blocking.
