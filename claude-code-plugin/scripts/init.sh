#!/usr/bin/env bash
# Scaffold the files the Lakebridge Claude Code plugin works from, under ./migration.
# The directory name is fixed because the plugin's prompts reference it.
# Existing files are never overwritten.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="migration"

mkdir -p "$TARGET"/{specs,reviews,fixes,sessions,ralph,analysis}

copy() {
  if [ -e "$2" ]; then
    echo "skip   $2 (exists)"
  else
    cp "$1" "$2"
    echo "create $2"
  fi
}

copy "$PLUGIN_ROOT/templates/migration.config.yml" "$TARGET/migration.config.yml"
copy "$PLUGIN_ROOT/templates/queue.md" "$TARGET/queue.md"
for f in "$PLUGIN_ROOT"/templates/ralph/*.md; do
  copy "$f" "$TARGET/ralph/$(basename "$f")"
done

echo
echo "Next: fill in $TARGET/migration.config.yml, then run /lakebridge:prime"
