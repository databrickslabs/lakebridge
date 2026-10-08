---
description: Show how to start one of the Lakebridge Ralph loops (migrate-queue, parity-sweep, stabilize-job)
argument-hint: "[migrate-queue|parity-sweep|stabilize-job]"
---

# Start a Lakebridge Ralph loop

Ralph loops need the `ralph-wiggum` plugin from the official Claude Code marketplace, which
provides `/ralph-loop` and `/cancel-ralph`. If `/ralph-loop` isn't available, tell the user to run:

```
/plugin marketplace add anthropics/claude-code
/plugin install ralph-wiggum@claude-code-plugins
```

Loops available (prompt files are copied into `migration/ralph/` by `/lakebridge:init`):

| Loop | Prompt file | Completion promise | Suggested max iterations |
|---|---|---|---|
| migrate-queue | `migration/ralph/migrate-queue.md` | `ALL_OBJECTS_MIGRATED` | 2 × queue rows |
| parity-sweep | `migration/ralph/parity-sweep.md` | `PARITY_SWEEP_COMPLETE` | 3 × tables |
| stabilize-job | `migration/ralph/stabilize-job.md` | `JOB_GREEN` | 10 |

For the loop in `$ARGUMENTS` (or ask which one):

1. Check the prompt file exists; if not, suggest `/lakebridge:init`.
2. For `stabilize-job`, ask which job and replace `<JOB>` in the prompt file.
3. Check preconditions: `/lakebridge:prime` passes, the git working tree is clean, the user is on a
   branch they're happy for the loop to commit to, and `.claude/ralph-loop.local.md` is git-ignored
   (the loop's state file must not be committed).
4. Give the user the exact command to run, for example:

```
/ralph-loop "Follow the instructions in migration/ralph/migrate-queue.md" --completion-promise "ALL_OBJECTS_MIGRATED" --max-iterations 60
```

Don't start the loop yourself. Remind the user that `/cancel-ralph` stops it, and that the loop commits
after every iteration, so `git log` shows its progress.
