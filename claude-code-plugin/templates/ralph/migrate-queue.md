# Ralph loop: migrate the queue

Completion promise: `ALL_OBJECTS_MIGRATED`

You are running inside a Ralph loop: this same prompt is fed back after every iteration. Your
memory between iterations is the files in this repository — the queue, the session files and git
history — not the conversation. Do exactly one object per iteration.

## Each iteration

1. **Orient.** Read `migration/migration.config.yml` and the queue at `paths.queue`. Print one line:
   `Progress: <PASS>/<total> PASS, <BLOCKED> BLOCKED, <PENDING> PENDING`.
2. **Recover.** If a row is `IN_PROGRESS`, the previous iteration was interrupted: resume that object
   (the `migrate-object` skill resumes from its session file).
3. **Pick.** Otherwise pick the first `PENDING` row, by lowest priority, whose `Depends on` objects
   are all `PASS`. Set it to `IN_PROGRESS`, increment `Attempts`, and commit the queue.
4. **Migrate.** Run the `migrate-object` skill for that object.
5. **Record.** The skill sets the row to `PASS`, `FAIL` or `BLOCKED` with a one-line note. Then: a
   `FAIL` row with `Attempts` = 1 goes back to `PENDING` for one fresh attempt (new context often
   helps); a `FAIL` row with `Attempts` ≥ 2 becomes `BLOCKED`.
6. **Commit.** If the skill left anything uncommitted, `git add` the queue, plans, reports, session
   files and code and commit `migrate <object>: <STATUS>`. Never commit `.claude/ralph-loop.local.md`.
7. **End the iteration.** Stop here; the loop will start the next one.

## Escape hatches

- **Infrastructure problem** (auth, permissions, workspace or source unreachable): don't burn
  iterations. Mark nothing, write the error to `migration/ralph/STOPPED.md`, and output the
  completion promise so a human can fix it. The note must say the loop stopped on an infrastructure
  error, not that the work is done.
- **Nothing runnable** (every remaining row is `BLOCKED` or waiting on a `BLOCKED` dependency): write a
  summary of blockers to `migration/ralph/STOPPED.md` and output the promise.

## Done

Output `<promise>ALL_OBJECTS_MIGRATED</promise>` only when one of these is true:
- no `PENDING` or `IN_PROGRESS` rows remain, and every non-`PASS` row has a note explaining why; or
- an escape hatch above applied and `migration/ralph/STOPPED.md` explains it.

Never output the promise to get out of a hard object. Mark it `BLOCKED` and continue instead.
