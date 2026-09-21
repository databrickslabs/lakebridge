#!/usr/bin/env bash
# Bundle the profiler e2e credentials into ONE base64 blob for a single GitHub Actions secret.
#
# It reads your local, git-ignored credentials and prints/copies a base64 string. It stores no
# secrets itself and is safe to commit. Re-run it whenever you rotate a credential.
#
#   Local files  ->  tar + base64  ->  one secret  ->  (in CI) base64 -d + tar -x  ->  files
#
# Usage:  ./bundle_credentials.sh
# Then paste the result into the repo secret named below.
set -euo pipefail

SECRET_NAME="LAKEBRIDGE_E2E_CREDENTIALS"
CREDS_DIR="${LAKEBRIDGE_CREDS_DIR:-$HOME/.databricks/labs/lakebridge}"
CRED_FILE=".credentials.yml"
BQ_KEY="bigquery-sa.json"   # optional; only needed to exercise the BigQuery e2e test

if [ ! -f "$CREDS_DIR/$CRED_FILE" ]; then
  echo "error: $CREDS_DIR/$CRED_FILE not found." >&2
  echo "       Fill credentials.template.yml and save it there first." >&2
  exit 1
fi

files=("$CRED_FILE")
if [ -f "$CREDS_DIR/$BQ_KEY" ]; then
  files+=("$BQ_KEY")
else
  echo "note: $CREDS_DIR/$BQ_KEY not present — skipping BigQuery key (fine if not testing BigQuery)." >&2
fi

blob="$(tar czf - -C "$CREDS_DIR" "${files[@]}" | base64 | tr -d '\n')"

if command -v pbcopy >/dev/null 2>&1; then
  printf '%s' "$blob" | pbcopy
  echo "Copied ${#blob}-char base64 bundle to clipboard. Paste it into GitHub secret ${SECRET_NAME}."
elif command -v xclip >/dev/null 2>&1; then
  printf '%s' "$blob" | xclip -selection clipboard
  echo "Copied base64 bundle to clipboard (xclip). Paste it into GitHub secret ${SECRET_NAME}."
else
  printf '%s\n' "$blob"
  echo "(no clipboard tool found — copy the base64 string above into GitHub secret ${SECRET_NAME})" >&2
fi
