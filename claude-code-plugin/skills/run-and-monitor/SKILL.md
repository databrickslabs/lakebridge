---
name: run-and-monitor
description: Run a Databricks job, monitor it task by task, classify failures, write a fix plan, apply safe fixes and rerun — with a hard limit on attempts. Use when asked to run, monitor, stabilize or self-heal a migration job.
---

# Run and monitor a Databricks job (self-healing, bounded)

## Input

`JOB`: a job name from `jobs` in `migration/migration.config.yml`. Optional `--plan <fix-plan>` to
apply a fix plan from an earlier failed run first. Optional `--max-iterations <n>` (default 3).

## 1. Preflight

- `databricks auth describe --profile <p>` succeeds.
- Resolve the job id by name: `databricks jobs list --profile <p> --output json`. Exactly one match,
  or stop and ask.
- Check each task's notebook/file path is the git-backed deploy path (see `databricks.deploy_method`).
  A task pointing at a stale copy explains "fixed but still failing".
- `git status` is clean and the deployed commit matches `HEAD`. If not, deploy first.

## 2. Run

`databricks jobs run-now <job_id> --no-wait --profile <p> --output json` → `run_id`.

## 3. Monitor

Poll `databricks jobs get-run <run_id> --profile <p> --output json` every 30–60 seconds. Report
task state changes only, not every poll. For each failed task, fetch
`databricks jobs get-run-output <task_run_id> --profile <p>` and keep the error and the last
stack-trace lines.

If a failure makes the rest of the run pointless (for example, the first ingestion task failed),
cancel the run: `databricks jobs cancel-run <run_id> --profile <p>`.

## 4. Classify and plan

Put every error into one category:

| Category | Examples | Auto-fixable? |
|---|---|---|
| `schema` | column not found, type mismatch, ambiguous reference | Usually |
| `data` | cast failure on specific values, NULL in non-null column, duplicate key in MERGE | Often, after checking the data |
| `code` | Python/SQL bug, unsupported-on-serverless API, import error | Usually |
| `dependency` | upstream table missing or empty | Only by running the upstream task |
| `infra` | permissions, secrets, networking, quota, cluster start | **No** — report to the user |

Write `<paths.fixes>/<job>-<YYYYMMDD-HHMM>.md` with, for each error: task, category, evidence, root
cause, the specific change, and whether it's auto-fixable.

## 5. Fix and rerun (max 3 iterations unless `--max-iterations` says otherwise)

1. Apply only the auto-fixable changes. Edit in place; commit; deploy.
2. Rerun only the failed tasks with `databricks jobs repair-run <run_id> --rerun-all-failed-tasks --profile <p>`
   (or `--json '{"rerun_tasks": ["task_a"]}'` for specific tasks). Start a new run instead when
   the fix changed an upstream task.
3. Stop when:
   - all tasks succeed → `SUCCESS`;
   - 3 iterations done → `MAX_ITERATIONS`;
   - the same tasks fail with the same errors as the previous iteration → `NO_PROGRESS`;
   - only `infra` or non-auto-fixable errors remain → `NEEDS_USER`.
4. If a fix makes things worse, `git revert` it, redeploy, and stop with `NO_PROGRESS`.

## Report

```
<job> run <run_id>: SUCCESS | MAX_ITERATIONS | NO_PROGRESS | NEEDS_USER
Iterations: <n>
Fixed:      <task: cause → change (commit)>
Remaining:  <task: category, error, what the user must do>
Fix plan:   <path>
```
