# Lakebridge plugin for Claude Code

Agentic migration workflows for [Claude Code](https://docs.claude.com/en/docs/claude-code) on top of
Lakebridge. The plugin is prompts only: no Python, no extra dependencies. It drives the
`databricks labs lakebridge` and `databricks` CLIs you already use.

Full guide: [Agentic Migration with Claude Code](https://databrickslabs.github.io/lakebridge/docs/agentic_migration).

## Install

```
/plugin marketplace add databrickslabs/lakebridge
/plugin install lakebridge@lakebridge
```

For the Ralph loops, also install Anthropic's `ralph-wiggum` plugin:

```
/plugin marketplace add anthropics/claude-code
/plugin install ralph-wiggum@claude-code-plugins
```

## Contents

| Type | Name | Purpose |
|---|---|---|
| Command | `/lakebridge:init` | Scaffold `migration/migration.config.yml`, the queue and loop prompts |
| Command | `/lakebridge:prime` | Load config, queue and recent reviews; check auth; state the migration rules |
| Command | `/lakebridge:ralph` | Explain and prepare a Ralph loop |
| Expert | `/lakebridge:expert-lakebridge` | Lakebridge CLI: analyze, transpile, llm-transpile, profiler, reconcile |
| Expert | `/lakebridge:expert-databricks` | Serverless, access-mode, Delta, ID/time-zone and deployment pitfalls |
| Expert | `/lakebridge:expert-tsql-to-spark` | T-SQL type, function and procedural-pattern conversions |
| Subagent | `migration-planner` | Reads the source object, runs transpile, writes a conversion plan |
| Subagent | `migration-builder` | Implements, deploys, runs and validates the plan |
| Subagent | `migration-reviewer` | Read-only review with risk tiers and a PASS/FAIL verdict |
| Subagent | `migration-fixer` | Fixes BLOCKER and HIGH issues, revalidates |
| Skill | `migrate-object` | Orchestrates plan → build → review → fix for one object |
| Skill | `parity-fix` | Schema first, then per-layer counts, then key-level diffs, then the fix |
| Skill | `run-and-monitor` | Run a job, classify failures, apply safe fixes, rerun — at most 3 times |
| Loop | `migrate-queue` | Work through the queue, one object per iteration |
| Loop | `parity-sweep` | Fix mismatched tables one at a time until all match or are explained |
| Loop | `stabilize-job` | Run and fix a job until it succeeds twice in a row |

## Configuration

All project facts live in `migration/migration.config.yml` in your repository: CLI profile, catalog
and schemas, source directories, jobs, output paths and rules. Keep secrets out of it — reference
CLI profiles, secret scopes and environment variables by name.
