# Installation

## Prerequisites[​](#prerequisites "Direct link to Prerequisites")

| Requirement              | Details                                                                                                               |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| **Databricks workspace** | Any workspace (production, development, or [free trial](https://www.databricks.com/try-databricks))                   |
| **Databricks CLI**       | [Install here](https://docs.databricks.com/en/dev-tools/cli/install.html) and configure with PAT or Service Principal |
| **Python**               | 3.10.1 – 3.13.x (Python 3.14 not supported)                                                                           |
| **Java**                 | Java 11 or above (required for the Morpheus transpiler)                                                               |
| **Network access**       | GitHub, Maven Central (`repo1.maven.org`), PyPI                                                                       |

Restricted environments

Hardened & Security-Restricted Environments If you are operating in a hardened environment with internet restrictions, firewall rules, or security policies, you must whitelist the following resources before installation:

* **GitHub:** `github.com`, `raw.githubusercontent.com` - For Lakebridge source code
* **Maven Central:** `repo1.maven.org`, `central.sonatype.com` - For transpiler plugins
* **PyPI:** `pypi.org`, `files.pythonhosted.org` - For Python packages
* **Python Downloads:** `python.org` - If installing Python
* **Java Downloads:** `oracle.com` or OpenJDK mirrors - If installing Java

Action Required: Contact your IT Security, CyberSecOps, or Infrastructure team to request whitelisting. Consider setting up a private repository/artifact mirror for organizations with strict internet access policies.

### Configure the Databricks CLI[​](#configure-the-databricks-cli "Direct link to Configure the Databricks CLI")

Install and authenticate the CLI:

* MacOS
* Windows
* Linux without brew

```shell
brew tap databricks/tap
brew install databricks

```

```shell
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/v0.299.0/install.sh

```

```bash
#!/usr/bin/env bash
apt update && apt install -y curl sudo unzip
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/v0.299.0/install.sh | sudo sh

```

Authenticate the CLI:

```bash
databricks configure

```

Verify connectivity: `databricks clusters list`

***

## Install Lakebridge[​](#install-lakebridge "Direct link to Install Lakebridge")

```bash
databricks labs install lakebridge

```

To use a specific profile:

```bash
databricks labs install lakebridge --profile <profile_name>

```

![lakebridge-install](/lakebridge/img/Install_Lakebridge.gif)

Verify:

```bash
databricks labs lakebridge --help

```

***

## Install Transpile[​](#install-transpile "Direct link to Install Transpile")

```bash
databricks labs lakebridge install-transpile

```

The command will prompt for your source dialect, input/output paths, and target technology.

To install Switch (the LLM transpiler):

```bash
databricks labs lakebridge install-transpile --include-llm-transpiler true

```

Override the default BladeBridge config

During `install-transpile` you can supply a custom config file for BladeBridge:

```text
Specify the config file to override the default[Bladebridge] config: <path>/custom_config.json

```

Verify:

```bash
databricks labs lakebridge transpile --help

```

***

## Configure Reconcile[​](#configure-reconcile "Direct link to Configure Reconcile")

```bash
databricks labs lakebridge configure-reconcile

```

The command will prompt for your source connection and Databricks catalog to reconcile, and install Lakebridge and create the required workspace resources to run Reconcile.

If you don't have permission to create SQL warehouses or clusters, add a `warehouse_id` or a `cluster_id` to your Databricks CLI profile:

```text
[profile-name]
host         = <your-workspace-url>
warehouse_id = <your-warehouse-id>
cluster_id = <your-cluster-id>

```

Verify:

```bash
databricks labs lakebridge reconcile --help

```

***

## Service Principal Setup (Optional)[​](#service-principal-setup-optional "Direct link to Service Principal Setup (Optional)")

For automated/production deployments, use a Service Principal instead of a Personal Access Token. See the [Databricks CLI authentication docs](https://docs.databricks.com/aws/en/dev-tools/cli/authentication) for setup instructions.
