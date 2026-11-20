# Ralph loop: parity sweep

Completion promise: `PARITY_SWEEP_COMPLETE`

You are running inside a Ralph loop. Your memory is the files in this repo. Fix one table per iteration.

## State file

`migration/parity.md` — create it on the first iteration from the latest reconcile results
(`databricks labs lakebridge reconcile`, `report_type: all`) or from the queue's `PASS` rows:

| Table | Legacy | Schema | Row Δ | Key mismatches | Status | Attempts | Notes |
|---|---|---|---|---|---|---|---|

Status values: `MISMATCH`, `IN_PROGRESS`, `FIXED`, `ACCEPTED`, `NEEDS_USER`.

## Each iteration

1. Read the state file. Print `Parity: <FIXED+ACCEPTED>/<total>`.
2. Pick the `IN_PROGRESS` row if any, else the first `MISMATCH` row. Prefer tables that others depend
   on (dimensions before facts): fixing upstream often fixes downstream.
3. Run the `parity-fix` skill for it.
4. Update the row with the result, new counts and a one-line root cause. After 3 attempts without
   progress, set `NEEDS_USER`.
5. Commit: `parity <table>: <STATUS>`.
6. End the iteration.

## Rules

- Never copy legacy rows into the target to close a gap without explicit user approval. Mark the row
  `NEEDS_USER` and explain.
- If a fix to one table changes others, re-check them and update their rows in the same iteration.

## Done

Output `<promise>PARITY_SWEEP_COMPLETE</promise>` when no `MISMATCH` or `IN_PROGRESS` rows remain.
Every `ACCEPTED` and `NEEDS_USER` row must have evidence in its notes.
