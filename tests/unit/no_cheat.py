import sys
from pathlib import Path

# === Suppression bypass tags ===
# Each tag represents a different way a developer can silence a linter.
# Adding any of these to code without explicit policy approval is disallowed.
# Note: substring matching means a comment like "# See
# false positive for NOQA_TAG. This is intentionally accepted — such wording is
# extremely rare in practice.

PYLINT_DISABLE_TAG = '# pylint: disable='
PYLINT_DISABLE_NEXT_TAG = '# pylint: disable-next='

# A single noqa tag (the string stored in NOQA_TAG) catches all three inline ruff suppression
# forms: bare (no code), colon form (with code after colon), and space form (with code after space).
# It is NOT a substring of the ruff file-level tag (which starts with "# ruff:"),
# so NOQA_TAG won't double-count file-level ruff suppressions.
NOQA_TAG = '# noqa'

# Ruff file-level suppression tag (RUFF_NOQA_TAG): placed at top of a file,
# suppresses a specific rule or all rules for the entire file.
RUFF_NOQA_TAG = '# ruff: noqa'

# Both disable= and disable-next= allow the same cyclic-import pair.
# This exception exists because cyclic imports in a large SDK codebase are
# sometimes unavoidable and are only acceptable when BOTH codes appear together
# (indicating an intentional local-import pattern).
ALLOWED_PYLINT_CYCLIC = {'cyclic-import', 'import-outside-toplevel'}


def _strip_code(code: str) -> str:
    return code.strip().strip('\n').strip('"').strip("'")


def _check_pylint_tag(lines: list[str], tag: str, allowed: set[str]) -> list[str]:
    """Check pylint disable tags that support per-code allowlisting."""
    removed: dict[str, int] = {}
    added: dict[str, int] = {}
    for line in lines:
        if not (line.startswith('-') or line.startswith('+')):
            continue
        idx = line.find(tag)
        if idx < 0:
            continue
        codes = {_strip_code(c) for c in line[idx + len(tag) :].split(',')}
        codes_not_in_allowed = codes - allowed
        if codes_not_in_allowed:
            # Disallowed codes present: strip allowed members, flag only the disallowed ones
            codes = codes_not_in_allowed
        elif len(codes & allowed) == len(allowed):
            # Full allowed pair and nothing else: permit this usage
            codes = set()
        for code in codes:
            if line.startswith('-'):
                removed[code] = removed.get(code, 0) + 1
            else:
                added[code] = added.get(code, 0) + 1
    results = []
    for code, count in added.items():
        net = count - removed.get(code, 0)
        if net > 0:
            results.append(f"Do not cheat the linter: found {net} additional {tag}{code}")
    return results


def _check_generic_tag(lines: list[str], tag: str) -> list[str]:
    """Check suppression tags where any net addition is a violation (no allowlist)."""
    removed = 0
    added = 0
    for line in lines:
        if not (line.startswith('-') or line.startswith('+')):
            continue
        if tag not in line:
            continue
        if line.startswith('-'):
            removed += 1
        else:
            added += 1
    net = added - removed
    if net > 0:
        return [f"Do not cheat the linter: found {net} additional '{tag}' suppression(s)"]
    return []


def no_cheat(diff_text: str) -> str:
    lines = diff_text.split('\n')
    results: list[str] = []
    results.extend(_check_pylint_tag(lines, PYLINT_DISABLE_TAG, ALLOWED_PYLINT_CYCLIC))
    results.extend(_check_pylint_tag(lines, PYLINT_DISABLE_NEXT_TAG, ALLOWED_PYLINT_CYCLIC))
    results.extend(_check_generic_tag(lines, NOQA_TAG))
    results.extend(_check_generic_tag(lines, RUFF_NOQA_TAG))
    return '\n'.join(results)


if __name__ == "__main__":
    diff_data = sys.argv[1]
    path = Path(diff_data)
    if path.exists():
        diff_data = path.read_text("utf-8")
    print(no_cheat(diff_data))
