"""
In-memory SQL substitution engine for the BigQuery profiler.

Applies line-level string substitutions defined in `substitutions.json` to the
vendored SQL templates before they're sent to BigQuery. No SQL parsing involved —
this is plain text replacement against per-file rule tables.

Ported from the upstream GCP-native BQ profiler `automation/src/compiler.py` with two
adaptations:
  * No filesystem I/O — `substitute()` accepts raw SQL text and returns the substituted text.
  * Substitutions are applied whenever the user provides the corresponding variable:
    presence of the variable in the constructor kwargs is the signal of intent. (Rules
    whose variable is absent are skipped and the SQL keeps its baked-in default.)
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SqlSubstituter:
    def __init__(self, substitutions: list[dict[str, Any]], **variables: Any) -> None:
        """
        :param substitutions: Parsed contents of substitutions.json (list of per-file rule blocks).
        :param variables: Keyword args providing values for substitution variables. Must include
                          `project_region` (required). Optional: `profiling_window_in_days`,
                          `time_format`, plus any future additions.
        """
        if not variables.get("project_region"):
            raise ValueError("The 'project_region' variable is required but was not provided.")
        self._rules_by_filename: dict[str, list[dict[str, Any]]] = {}
        for rule in substitutions:
            filename = rule["file_path"].split("/")[-1]
            self._rules_by_filename[filename] = rule.get("substitutions", [])
        self._variables = variables

    @classmethod
    def from_json(cls, substitutions_json: str, **variables: Any) -> "SqlSubstituter":
        return cls(json.loads(substitutions_json), **variables)

    def substitute(self, sql_filename: str, raw_sql: str) -> str:
        """
        Apply the substitution rules for `sql_filename` to `raw_sql` and return the result.

        Lines matching `search_text` get `find_text` → variable-value substitution. Substitutions
        without a matching variable are skipped (the SQL keeps its baked-in default).
        """
        rules = self._rules_by_filename.get(sql_filename, [])
        if not rules:
            logger.debug(f"No substitution rules for {sql_filename}; using SQL as-is.")
            return raw_sql

        lines = raw_sql.splitlines(keepends=True)
        for sub in rules:
            var_name = sub.get("replace_with_var")
            if var_name not in self._variables:
                continue
            replacement = str(self._variables[var_name])
            search_text = sub["search_text"]
            find_text = sub["find_text"]
            for idx, line in enumerate(lines):
                if search_text in line:
                    lines[idx] = line.replace(find_text, replacement)
                    break
        return "".join(lines)
