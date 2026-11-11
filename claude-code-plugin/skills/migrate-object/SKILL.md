---
name: migrate-object
description: Migrate one legacy SQL object (stored procedure, view, function or table load) to Databricks end to end by orchestrating plan, build, review and fix subagents. Use when asked to migrate or convert a specific object, or from the migrate-queue Ralph loop.
---

# Migrate one object: plan → build → review → fix

You are the **orchestrator**. Delegate each phase to its subagent and pass file paths between
them. Don't do the phases' work yourself — this keeps each phase's context small and lets the
reviewer judge the builder's output independently.

## Input

`OBJECT`: the legacy object name, from the user or the queue row. If none is given, ask.

## Phase 0 — Preconditions

1. Read `migration/migration.config.yml`. If missing, stop and suggest `/lakebridge:init`.
2. Find the object's source file in `source.objects_dir`. If missing, stop.
3. Create a session file `<paths.sessions>/<object-kebab>.json`:
   ```json
   {"object": "...", "attempt": 1, "phases": {}, "status": "IN_PROGRESS"}
   ```
   If one exists with status `IN_PROGRESS`, resume from its last completed phase.

## Phase 1 — Plan

Spawn the `migration-planner` subagent with the object name and config path. Record the plan
path in the session. If the plan lists blockers (missing dependencies, missing source), stop with
status `BLOCKED` and the blocker list.

## Phase 2 — Build

Spawn the `migration-builder` subagent with the plan path. Record changed files, run id and
validation results. If the run failed and the builder could not recover, continue to review
anyway — the reviewer will classify the failure.

## Phase 3 — Review

Spawn the `migration-reviewer` subagent with the plan path and object name. Record the report
path and verdict.

## Phase 4 — Fix (only on FAIL)

Spawn the `migration-fixer` subagent with the review report path. Then go back to Phase 3 with a
fresh reviewer. Repeat until the verdict is `PASS` or you reach `rules.max_fix_attempts`
(default 3).

Stop early and mark `BLOCKED` when:
- the fixer reports `NEEDS_USER` items, or
- two consecutive reviews list the same BLOCKER issues (no progress).

## Phase 5 — Finish

Update the session file (`PASS`, `FAIL` or `BLOCKED`), and if a queue file exists update the
object's row: status and a one-line note (verdict, or the blocker). Commit the queue, plan,
reports and session file together. With `rules.git_workflow: pr`, open a pull request for the
object's commits when the status is `PASS`; with `direct`, the commits on the current branch are
the result.

## Report

```
<object>: PASS | FAIL | BLOCKED
Plan:    <path>
Code:    <paths>
Review:  <path> (<verdict>, attempt <n>)
Fix:     <path or none>
Run:     <run id / status>
Next:    <what the user needs to do, if anything>
```
