#!/bin/bash
# Install the registry proxy hook into the lakebridge venv.
# This redirects Maven Central and PyPI requests through Databricks internal proxies.
# Run this after `databricks labs install lakebridge` or `databricks labs upgrade lakebridge`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Auto-detect Python version in venv
VENV_BASE="$HOME/.databricks/labs/lakebridge/state/venv/lib"
PYTHON_DIR=$(ls -1 "$VENV_BASE" 2>/dev/null | head -1)

if [ -z "$PYTHON_DIR" ]; then
    echo "ERROR: Could not find lakebridge venv at $VENV_BASE"
    echo "Make sure lakebridge is installed: databricks labs install lakebridge"
    exit 1
fi

SITE_PACKAGES="$VENV_BASE/$PYTHON_DIR/site-packages"

echo "Installing registry proxy hook to: $SITE_PACKAGES"
cp "$SCRIPT_DIR/_registry_proxy_patcher.py" "$SITE_PACKAGES/"
cp "$SCRIPT_DIR/registry_proxy.pth" "$SITE_PACKAGES/"
echo "Done. Maven Central -> maven-proxy.dev.databricks.com, PyPI -> pypi-proxy.dev.databricks.com"
