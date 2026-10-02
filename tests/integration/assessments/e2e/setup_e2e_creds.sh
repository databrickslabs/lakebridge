#!/usr/bin/env bash
# Download the profiler e2e credentials from a Unity Catalog volume to the local lakebridge
# credentials path, for BOTH local runs and CI. Nothing secret lives in the repo.
#
#   UC volume  --(databricks fs cp)-->  ~/.databricks/labs/lakebridge/
#     .credentials.yml   (required; all 7 sources)
#     snowflake.pem      (optional; Snowflake key-pair auth)
#     bigquery-sa.json   (optional; BigQuery service-account key)
#
# Auth uses your ambient Databricks credentials — a ~/.databrickscfg profile locally (pass one
# with -p, or via $DATABRICKS_CONFIG_PROFILE), or DATABRICKS_HOST + a token/OAuth env on CI.
# The tests then read the default credentials path; no code changes needed.
#
# Usage:
#   LAKEBRIDGE_E2E_VOLUME=dbfs:/Volumes/<catalog>/<schema>/<volume>/lakebridge-e2e ./setup_e2e_creds.sh
#   ./setup_e2e_creds.sh -p my-cli-profile        # local, with a named CLI profile
set -euo pipefail

# --- config -----------------------------------------------------------------------------------
# The volume DIRECTORY holding the filled creds. MUST use the dbfs: scheme — `databricks fs cp`
# requires it for UC Volumes. Set LAKEBRIDGE_E2E_VOLUME, or edit this default.
VOLUME="${LAKEBRIDGE_E2E_VOLUME:-dbfs:/Volumes/<catalog>/<schema>/<volume>/lakebridge-e2e}"

CRED_FILE=".credentials.yml"     # required
SNOWFLAKE_KEY="snowflake.pem"    # optional — Snowflake key-pair auth
BQ_KEY="bigquery-sa.json"        # optional — BigQuery service-account key

DEST="$HOME/.databricks/labs/lakebridge"

PROFILE_ARGS=()
while getopts "p:" opt; do
  case "$opt" in
    p) PROFILE_ARGS=(-p "$OPTARG") ;;
    *) echo "usage: $0 [-p <databricks-cli-profile>]" >&2; exit 2 ;;
  esac
done

if [[ "$VOLUME" == *"<catalog>"* ]]; then
  echo "error: set LAKEBRIDGE_E2E_VOLUME (or edit VOLUME) to your real dbfs:/Volumes/... path." >&2
  exit 2
fi
command -v databricks >/dev/null 2>&1 || { echo "error: databricks CLI not found on PATH." >&2; exit 1; }

mkdir -p "$DEST"

# fetch <name-in-volume> <required: 1|0>
fetch() {
  local name="$1" required="$2" err
  if err="$(databricks "${PROFILE_ARGS[@]}" fs cp "$VOLUME/$name" "$DEST/$name" --overwrite 2>&1)"; then
    echo "  ok   $name"
  elif [[ "$required" == "1" ]]; then
    echo "error: could not download required $VOLUME/$name (check auth and the volume path):" >&2
    echo "$err" >&2
    exit 1
  else
    echo "  skip $name (not in volume; needed only when its opt-in e2e is enabled)"
  fi
}

echo "Downloading e2e credentials:  $VOLUME  ->  $DEST"
fetch "$CRED_FILE" 1
fetch "$SNOWFLAKE_KEY" 0
fetch "$BQ_KEY" 0

# BigQuery reads GOOGLE_APPLICATION_CREDENTIALS, not the creds file — wire it to the SA key.
if [[ -f "$DEST/$BQ_KEY" ]]; then
  if [[ -n "${GITHUB_ENV:-}" ]]; then
    echo "GOOGLE_APPLICATION_CREDENTIALS=$DEST/$BQ_KEY" >> "$GITHUB_ENV"   # CI: persist to later steps
    echo "  ok   GOOGLE_APPLICATION_CREDENTIALS wired via \$GITHUB_ENV"
  else
    echo "  note for local BigQuery runs, export:"
    echo "         export GOOGLE_APPLICATION_CREDENTIALS=$DEST/$BQ_KEY"
  fi
fi

echo "Done. Credentials are at $DEST/$CRED_FILE"
