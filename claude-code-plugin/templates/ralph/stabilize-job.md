# Ralph loop: stabilize a job

Completion promise: `JOB_GREEN`

You are running inside a Ralph loop. Replace `<JOB>` with a job name from `migration.config.yml`.

## Each iteration

1. Read `migration/ralph/stabilize-log.md` (create it if missing). It lists previous runs, errors
   and fixes.
2. Run the `run-and-monitor` skill for `<JOB>` with **one** fix round (run → classify → fix →
   repair once). The loop, not the skill, provides the repetition, so each iteration starts with
   fresh context. Pass the latest fix plan with `--plan` if the previous iteration left one unapplied.
3. Append to the log: run id, result, errors by category, fixes applied (commit shas).
4. Commit the log and any fixes.
5. End the iteration.

## Stop conditions

- The job succeeds on **two consecutive** runs (one green run can be luck with incremental data):
  output `<promise>JOB_GREEN</promise>`.
- Only `infra` or `NEEDS_USER` errors remain, or the same errors repeat for 3 iterations: write what
  the user must do at the top of the log, then output the promise. The log must say the job is not green.
