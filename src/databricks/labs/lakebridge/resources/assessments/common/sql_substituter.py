import re
from typing import Any, Mapping

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def substitute(raw_sql: str, params: Mapping[str, Any]) -> str:
    """Fill every ``{{name}}`` placeholder in ``raw_sql`` from ``params`` and return the result.

    Values are substituted **verbatim, with no escaping or validation**. Callers are
    responsible for validating/sanitising every value before passing it in.

    Raises ``ValueError`` if any ``{{name}}`` in ``raw_sql`` has no corresponding key in
    ``params`` (the SQL author referenced a placeholder the caller did not supply).
    """
    missing: set[str] = set()

    def _replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in params:
            missing.add(name)
            return match.group(0)
        return str(params[name])

    result = _PLACEHOLDER.sub(_replace, raw_sql)

    if missing:
        names = ", ".join("{{" + n + "}}" for n in sorted(missing))
        raise ValueError(f"Unsubstituted placeholder(s) in SQL: {names}")

    return result
