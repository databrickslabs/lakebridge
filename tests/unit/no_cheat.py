# ruff: noqa: PLW2901
import sys
from pathlib import Path

DISABLE_TAG = '# ruff: noqa'


def _strip_code(code: str) -> str:
    return code.strip().strip('\n').strip('"').strip("'").lstrip(':').strip()


def no_cheat(diff_text: str) -> str:
    lines = diff_text.split('\n')
    removed: dict[str, int] = {}
    added: dict[str, int] = {}
    for line in lines:
        if not (line.startswith("-") or line.startswith("+")):
            continue
        idx = line.find(DISABLE_TAG)
        if idx < 0:
            continue
        codes = {_strip_code(code) for code in line[idx + len(DISABLE_TAG) :].split(',')}
        allowed_local_cyclic_imports = {'cyclic-import', 'import-outside-toplevel'}
        if len(codes.intersection(allowed_local_cyclic_imports)) == len(allowed_local_cyclic_imports):
            codes = codes.difference(allowed_local_cyclic_imports)
        for code in codes:
            if line.startswith("-"):
                removed[code] = removed.get(code, 0) + 1
                continue
            added[code] = added.get(code, 0) + 1
    results: list[str] = []
    for code, count in added.items():
        count -= removed.get(code, 0)
        if count > 0:
            results.append(f"Do not cheat the linter: found {count} additional {DISABLE_TAG}: {code}")
    return '\n'.join(results)


if __name__ == "__main__":
    diff_data = sys.argv[1]
    path = Path(diff_data)
    if path.exists():
        diff_data = path.read_text("utf-8")
    print(no_cheat(diff_data))
