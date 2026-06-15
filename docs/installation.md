# Installation

## Prerequisites[​](#prerequisites "Direct link to Prerequisites")

| Requirement              | Details                                                                                                               |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| **Databricks workspace** | Any workspace (production, development, or [free trial](https://www.databricks.com/try-databricks))                   |
| **Databricks CLI**       | [Install here](https://docs.databricks.com/en/dev-tools/cli/install.html) and configure with PAT or Service Principal |
| **Python**               | 3.10.1–3.14.x                                                                                                         |
| **Java**                 | Java 21 or above (required for the Morpheus transpiler)                                                               |
| **Network access**       | GitHub, Maven Central, PyPI                                                                                           |

### Python and Java[​](#python-and-java "Direct link to Python and Java")

If necessary:

* Python can be obtained [here](https://python.org/); if installing on Windows, please ensure you install the 64-bit version.
* Java can be obtained [here](https://adoptium.net/temurin/releases); the current LTS release is recommended.

To verify these are installed and available, from the terminal the following should work and display the installed versions:

```console
python -V
java -version

```

### Internet resources[​](#internet-resources "Direct link to Internet resources")

The installation below requires access to the following network resources:

| Site          | Hosts                                         | Purpose                                                                         |
| ------------- | --------------------------------------------- | ------------------------------------------------------------------------------- |
| GitHub        | `github.com`<br />`raw.githubusercontent.com` | Packages and metadata used for general installation and upgrades of Lakebridge. |
| Maven Central | `repo1.maven.org`                             | Installing and upgrading transpiler plugins.                                    |
| PyPI          | `pypi.org`<br />`files.pythonhosted.org`      |                                                                                 |

Support for proxies or mirrors to access these Internet resources can be configured.

#### General HTTP proxy configuration[​](#general-http-proxy-configuration "Direct link to General HTTP proxy configuration")

To configure general HTTP proxy for network access, set an environment variable named `https_proxy` to the URL of the HTTPS proxy.

* MacOS
* Windows
* Linux

```bash
export https_proxy=http://my-proxy.example.com:3128/

```

```command
set https_proxy=http://my-proxy.example.com:3128/

```

```bash
export https_proxy=http://my-proxy.example.com:3128/

```

Contact your IT team if necessary for information on the URL to use. If authentication is needed, refer to the [section below](#proxy-or-mirror-authentication).

#### Maven Central[​](#maven-central "Direct link to Maven Central")

If a local mirror should be used for downloading resources from Maven Central, set the `LAKEBRIDGE_MAVEN_URL` environment to the URL of the mirror.

* MacOS
* Windows
* Linux

```bash
export LAKEBRIDGE_MAVEN_URL=https://mirror.example.com/maven/releases/

```

```command
set LAKEBRIDGE_MAVEN_URL=https://mirror.example.com/maven/releases/

```

```bash
export LAKEBRIDGE_MAVEN_URL=https://mirror.example.com/maven/releases/

```

Contact your IT team if necessary for information on the URL to use. If authentication is needed, refer to the [section below](#proxy-or-mirror-authentication).

#### PyPI[​](#pypi "Direct link to PyPI")

If a local mirror should be used for downloaded resources from PyPI, this needs to be configured with pip:

```shell
pip3 config --user set global.index-url https://mirror.example.com/pypi

```

Contact your IT team if necessary for information on the URL to use. If authentication is needed, refer to the [section below](#proxy-or-mirror-authentication).

#### Proxy or Mirror Authentication[​](#proxy-or-mirror-authentication "Direct link to Proxy or Mirror Authentication")

If authentication is needed to access a mirror or proxy, a `~/.netrc` file can be used to specify the credentials to use. The format is of the form:

```netrc
machine my-proxy.example.com
login bobby
password tAble5

```

Note that `my-proxy.example.com` should be the host from the URL (and not the entire URL).

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

The command will prompt for your source connection and Databricks catalog to reconcile, and install Lakebridge and create the required workspace resources to run Reconcile. Optionally, the command can discover the tables in your source and generate a base config to run reconcile. This autoconfiguration should be reviewed before running reconcile.

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
