# Fix: BladeBridge Crash on Large DataStage XML Files

**Issue:** [#2097](https://github.com/databrickslabs/lakebridge/issues/2097)
**Affects:** BladeBridge plugin (`databricks-bb-plugin`) version 0.3.0 and earlier
**Symptom:** `OSError: [Errno 63] File name too long` when processing DataStage XML files larger than ~100 MB

---

## Quick Fix

Run the provided patch script after installing Lakebridge and BladeBridge:

```bash
bash scripts/patch_bladebridge_large_xml.sh
```

The script is idempotent — it detects if the patch is already applied and skips if so. It creates a backup of the original file before patching.

> **Note:** Re-run this script after upgrading BladeBridge (`databricks labs lakebridge install-transpile`), as upgrades reinstall the plugin from scratch.

---

## Manual Fix

If you prefer to patch manually:

**File:**
```
~/.databricks/labs/remorph-transpilers/bladebridge/lib/.venv/lib/python3.10/site-packages/databricks/labs/bladebridge/transpiler.py
```

**Line 203 — change:**
```python
# BEFORE:
                "-n",
                str(transpiled_dir.relative_to(workdir)),

# AFTER:
                "-n",
                str(transpiled_dir.absolute()),
```

**Then clear the bytecode cache:**
```bash
find ~/.databricks/labs/remorph-transpilers/bladebridge -name "*.pyc" -path "*transpiler*" -delete
```

---

## Root Cause

BladeBridge's Python wrapper (`transpiler.py`) invokes a compiled binary called `dbxconv` to convert DataStage jobs. The output directory is passed via the `-n` flag as a **relative path** (e.g., `-n transpiled`).

When `dbxconv` processes large XML files containing hundreds of jobs, it internally creates subdirectories using the same name passed to `-n`. Since `-n` receives a relative path, the binary resolves it relative to its own output on every internal write, creating recursive nesting:

```
transpiled/                  <-- created by Python wrapper
  transpiled/                <-- created by dbxconv
    transpiled/              <-- created by dbxconv
      transpiled/            <-- ...87+ levels deep
```

The total path length eventually exceeds the OS limit (1024 bytes on macOS), causing the crash.

**The fix** passes an **absolute path** to `-n` instead. When `dbxconv` receives an absolute path, it writes directly to that location without recursive nesting.

We verified this is not a name collision issue — renaming the directory to `bb_output` produced `bb_output/bb_output/bb_output/...` instead. The recursion is caused by the binary resolving the relative `-n` path repeatedly regardless of the directory name.

---

## Validation

Tested on a DataStage XML export (119 MB, 2.2 million lines, DataStage 11.5):

| Metric | Without Patch | With Patch |
|---|---|---|
| Result | Crashes after ~69 min | Completes in ~3 hours |
| Output files | 0 | **425** (379 notebooks + 46 workflow JSONs) |
| Recursive nesting | 87+ levels | 0 |
| Errors | Fatal `OSError: [Errno 63]` | 0 analysis, 0 parsing, 0 validation, 0 generation |

---

## Versions Tested

| Component | Version |
|---|---|
| Lakebridge | 0.12.2 |
| BladeBridge plugin (`databricks-bb-plugin`) | 0.3.0 |
| Databricks CLI | 0.291.0 |
| Python | 3.10.13 |
| macOS | Darwin 25.3.0 (Apple Silicon) |
