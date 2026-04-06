#!/usr/bin/env bash
# patch_bladebridge_large_xml.sh
#
# Fixes BladeBridge crash on DataStage XML files larger than ~100 MB.
# See: https://github.com/databrickslabs/lakebridge/issues/2097
#
# The BladeBridge plugin's dbxconv binary receives the output directory as a
# relative path via the -n flag. For large XML files, the binary recursively
# nests that directory name, creating transpiled/transpiled/transpiled/...
# until the path exceeds the OS limit (errno 63: File name too long).
#
# Fix: pass an absolute path to -n instead of a relative path.
#
# Usage:
#   bash scripts/patch_bladebridge_large_xml.sh
#
set -euo pipefail

PATCH_FILE="$HOME/.databricks/labs/remorph-transpilers/bladebridge/lib/.venv/lib/python3.10/site-packages/databricks/labs/bladebridge/transpiler.py"

echo "=== BladeBridge Large XML Patch (Issue #2097) ==="
echo ""

# Step 1: Check if the file exists
if [ ! -f "$PATCH_FILE" ]; then
    echo "ERROR: BladeBridge transpiler.py not found at:"
    echo "  $PATCH_FILE"
    echo ""
    echo "Make sure BladeBridge is installed:"
    echo "  databricks labs lakebridge install-transpile --interactive false"
    exit 1
fi

echo "Found: $PATCH_FILE"

# Step 2: Check if the patch is needed
if grep -q 'str(transpiled_dir\.relative_to(workdir))' "$PATCH_FILE"; then
    echo "Status: NEEDS PATCHING (relative path found)"
elif grep -q 'str(transpiled_dir\.absolute())' "$PATCH_FILE"; then
    echo "Status: ALREADY PATCHED (absolute path found)"
    echo "No changes needed."
    exit 0
else
    echo "WARNING: Could not find the expected code pattern."
    echo "The BladeBridge plugin version may be different from what this patch expects."
    echo "Please check the file manually."
    exit 1
fi

# Step 3: Create backup
cp "$PATCH_FILE" "${PATCH_FILE}.bak"
echo "Backup: ${PATCH_FILE}.bak"

# Step 4: Apply the patch
sed -i '' 's/str(transpiled_dir\.relative_to(workdir))/str(transpiled_dir.absolute())/' "$PATCH_FILE"

# Step 5: Verify the patch was applied
if grep -q 'str(transpiled_dir\.absolute())' "$PATCH_FILE"; then
    echo "Patch: APPLIED SUCCESSFULLY"
else
    echo "ERROR: Patch failed. Restoring backup..."
    cp "${PATCH_FILE}.bak" "$PATCH_FILE"
    exit 1
fi

# Step 6: Clear Python bytecode cache
find "$HOME/.databricks/labs/remorph-transpilers/bladebridge" -name "*.pyc" -path "*transpiler*" -delete 2>/dev/null
echo "Cache: CLEARED"

echo ""
echo "=== Done ==="
echo "BladeBridge can now process DataStage XML files larger than 100 MB."
echo "Tested on: FinancialCredit.xml (119 MB, 2.2M lines) -> 425 files, 0 errors."
