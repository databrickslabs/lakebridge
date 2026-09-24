---
description: Scaffold migration.config.yml, the migration queue and Ralph loop prompts in this project
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/init.sh:*)"]
---

# Initialize a Lakebridge agentic migration

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/init.sh"
```

Using the output above:

1. Open the generated `migration/migration.config.yml` and help the user fill it in. Ask only for values
   you cannot discover yourself. You can discover:
   - Databricks CLI profiles: `databricks auth profiles`
   - Catalogs and schemas: `databricks catalogs list --profile <p>`, `databricks schemas list <catalog> --profile <p>`
   - Jobs: `databricks jobs list --profile <p> --output json`
   - Installed transpilers: `databricks labs lakebridge describe-transpile`
2. Never write passwords, tokens or connection strings into the config. Reference CLI profiles,
   secret scopes and environment variables by name.
3. If the source SQL is in monolithic files, recommend the Lakebridge SQL Splitter first, then
   `databricks labs lakebridge analyze` to size the work.
4. Seed `migration/queue.md` from the analyzer report or the files in `source.objects_dir`: one row per
   object, priority by dependency order (tables and dimensions before facts, facts before views).
5. Finish by suggesting `/lakebridge:prime`.
