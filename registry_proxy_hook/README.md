# Registry Proxy Hook for Lakebridge

Workaround for the Databricks network policy that blocks direct access to:
- `repo.maven.apache.org` (Maven Central)
- `pypi.org` (PyPI)

This hook redirects lakebridge's registry requests through the Databricks internal proxies:
- Maven Central -> `https://maven-proxy.dev.databricks.com`
- PyPI -> `https://pypi-proxy.dev.databricks.com`

## How it works

Lakebridge hardcodes Maven Central and PyPI URLs in its Python source (`installers.py`).
Running `databricks labs upgrade lakebridge` reinstalls the package, overwriting any
direct source patches.

This hook uses a `.pth` file + `__import__` override to monkey-patch the URLs at
Python startup, surviving package reinstalls.

## Installation

```bash
./install.sh
```

Or manually copy the two files to the lakebridge venv:

```bash
VENV=~/.databricks/labs/lakebridge/state/venv/lib/python3.13/site-packages
cp _registry_proxy_patcher.py "$VENV/"
cp registry_proxy.pth "$VENV/"
```

Re-run `install.sh` after each `databricks labs upgrade lakebridge` if the upgrade
recreates the venv from scratch.

## Source change (installers.py)

The branch also includes a source-level change to `installers.py` that reads
`MAVEN_CENTRAL_MIRROR` and `PYPI_MIRROR` environment variables, falling back to
the original URLs. This is the "proper" fix if it were to be merged upstream.

Usage:
```bash
export MAVEN_CENTRAL_MIRROR=https://maven-proxy.dev.databricks.com/
export PYPI_MIRROR=https://pypi-proxy.dev.databricks.com
databricks labs upgrade lakebridge
```

## Reference

- Internal doc: [go/maven-registry-access](https://go/maven-registry-access)
- Slack: `#public-registry-access`
