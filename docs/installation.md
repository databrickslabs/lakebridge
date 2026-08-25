# Installation

Lakebridge can be installed in two ways. Pick the path that fits your environment:

* **[Desktop App](#desktop-app)** — a graphical installer for macOS and Windows. Best if you prefer a UI and want the fewest manual steps: it bundles the Databricks CLI and Python and installs Lakebridge for you. Available from release **v0.14.0** onwards.
* **[Command-line (CLI)](#command-line-cli)** — install via the Databricks CLI. Best for automation, headless or server environments, Linux, or if you already work in the terminal.

Both paths connect to a Databricks workspace, require **Java 21 or above** (for the Morpheus transpiler), and need [network access](#network-access-proxies-and-mirrors) to GitHub, Maven Central, and PyPI. Each flow below is self-contained — follow the one you chose.

***

## Desktop App[​](#desktop-app "Direct link to Desktop App")

The desktop app is a graphical installer for macOS and Windows. The installer is published as an asset on each [GitHub release](https://github.com/databrickslabs/lakebridge/releases), from **v0.14.0** onwards.

note

The desktop app has its own version line (e.g. `1.3.2`), which is independent of the Lakebridge release tag it ships with — so the version in the installer filename does not match the release version.

### Requirements[​](#requirements "Direct link to Requirements")

| Requirement              | Details                                                                                                     |
| ------------------------ | ----------------------------------------------------------------------------------------------------------- |
| **Databricks workspace** | Any workspace (production, development, or [free trial](https://www.databricks.com/try-databricks))         |
| **Java**                 | Java 21 or above (required for the Morpheus transpiler) — [download](https://adoptium.net/temurin/releases) |
| **Network access**       | GitHub, Maven Central, PyPI (see [Network access](#network-access-proxies-and-mirrors))                     |

note

Unlike the CLI installation, you do **not** need to pre-install the Databricks CLI, Python, or Lakebridge itself — the app bundles the Databricks CLI and Python, and installs Lakebridge for you on first run.

### Download[​](#download "Direct link to Download")

Grab the installer for your platform from the [latest release](https://github.com/databrickslabs/lakebridge/releases/latest). Choose the build that matches your operating system and CPU architecture:

| Platform | Apple Silicon / ARM                          | Intel / AMD (x64)                          |
| -------- | -------------------------------------------- | ------------------------------------------ |
| macOS    | `MacOS-Lakebridge-<app-version>-arm64.dmg`   | `MacOS-Lakebridge-<app-version>-x64.dmg`   |
| Windows  | `Windows-Lakebridge-<app-version>-arm64.exe` | `Windows-Lakebridge-<app-version>-x64.exe` |

Which architecture?

* **macOS:** Apple Silicon (M1/M2/M3/M4) uses `arm64`; older Intel Macs use `x64`.
* **Windows:** most machines use `x64`; use `arm64` only for ARM-based devices (e.g. Surface Pro X).

### Install[​](#install "Direct link to Install")

* MacOS
* Windows

1. Download the `.dmg` matching your Mac's architecture.
2. Open the `.dmg` and drag **Lakebridge** into your **Applications** folder.
3. Launch Lakebridge from Applications.

1) Download the `.exe` matching your machine's architecture.
2) Run the installer and follow the prompts.
3) Launch Lakebridge from the Start menu.

### First run[​](#first-run "Direct link to First run")

On first launch, the app walks you through everything needed to get started:

1. **Check prerequisites** — the app verifies Java is installed at a supported version. If it is missing, install [Java 21 or above](https://adoptium.net/temurin/releases) and re-check.
2. **Connect to Databricks** — enter your workspace URL and sign in. The app authenticates via OAuth in your browser and configures the Databricks CLI profile for you.
3. **Install Lakebridge** — the app installs the Lakebridge CLI (and its bundled Databricks CLI and Python) in the background.

Once these steps complete, you can run assessment, transpilation, and profiling directly from the app.

![Installing Lakebridge using the desktop app](/lakebridge/img/installation-desktop-app.gif)

***

## Command-line (CLI)[​](#command-line-cli "Direct link to Command-line (CLI)")

### Prerequisites[​](#prerequisites "Direct link to Prerequisites")

| Requirement              | Details                                                                                                               |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| **Databricks workspace** | Any workspace (production, development, or [free trial](https://www.databricks.com/try-databricks))                   |
| **Databricks CLI**       | [Install here](https://docs.databricks.com/en/dev-tools/cli/install.html) and configure with PAT or Service Principal |
| **Python**               | 3.10.1–3.14.x                                                                                                         |
| **Java**                 | Java 21 or above (required for the Morpheus transpiler)                                                               |
| **Network access**       | GitHub, Maven Central, PyPI (see [Network access](#network-access-proxies-and-mirrors))                               |

#### Python and Java[​](#python-and-java "Direct link to Python and Java")

If necessary:

* Python can be obtained [here](https://python.org/); if installing on Windows, please ensure you install the 64-bit version.
* Java can be obtained [here](https://adoptium.net/temurin/releases); the current LTS release is recommended.

To verify these are installed and available, from the terminal the following should work and display the installed versions:

```console
python -V
java -version

```

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

### Install Lakebridge[​](#install-lakebridge "Direct link to Install Lakebridge")

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

### Install Transpile[​](#install-transpile "Direct link to Install Transpile")

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

### Configure Reconcile[​](#configure-reconcile "Direct link to Configure Reconcile")

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

Upgrading from an older Reconcile schema

Upgrading Lakebridge migrates the reconcile `details` and `aggregate_details` metadata tables to a new schema. The original tables are preserved as `<catalog>.<schema>.details_backup` and `aggregate_details_backup`, so the raw rows are never lost.

The migration is not automatically resumable. If it fails midway, rerun the upgrade after restoring the original tables manually:

* If `details` no longer exists, rename the backup back and rerun: `ALTER TABLE <catalog>.<schema>.details_backup RENAME TO <catalog>.<schema>.details`
* If a new (empty or partial) `details` was already created, drop it, rename the backup back as above, then rerun.

Apply the same steps to `aggregate_details` / `aggregate_details_backup`. Once the migrated tables are verified, the `*_backup` tables can be dropped.

### Service Principal Setup (Optional)[​](#service-principal-setup-optional "Direct link to Service Principal Setup (Optional)")

For automated/production deployments, use a Service Principal instead of a Personal Access Token. See the [Databricks CLI authentication docs](https://docs.databricks.com/aws/en/dev-tools/cli/authentication) for setup instructions.

***

## Network access, proxies, and mirrors[​](#network-access-proxies-and-mirrors "Direct link to Network access, proxies, and mirrors")

Both installation paths need access to the following network resources to download Lakebridge and its transpiler plugins:

| Site          | Hosts                                         | Purpose                                                                         |
| ------------- | --------------------------------------------- | ------------------------------------------------------------------------------- |
| GitHub        | `github.com`<br />`raw.githubusercontent.com` | Packages and metadata used for general installation and upgrades of Lakebridge. |
| Maven Central | `repo1.maven.org`                             | Installing and upgrading transpiler plugins.                                    |
| PyPI          | `pypi.org`<br />`files.pythonhosted.org`      |                                                                                 |

Support for proxies or mirrors to access these Internet resources can be configured as shown below.

### General HTTP proxy configuration[​](#general-http-proxy-configuration "Direct link to General HTTP proxy configuration")

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

### Maven Central[​](#maven-central "Direct link to Maven Central")

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

### PyPI[​](#pypi "Direct link to PyPI")

If a local mirror should be used for downloaded resources from PyPI, this needs to be configured with pip:

```shell
pip3 config --user set global.index-url https://mirror.example.com/pypi

```

Contact your IT team if necessary for information on the URL to use. If authentication is needed, refer to the [section below](#proxy-or-mirror-authentication).

### Proxy or Mirror Authentication[​](#proxy-or-mirror-authentication "Direct link to Proxy or Mirror Authentication")

If authentication is needed to access a mirror or proxy, a `~/.netrc` file can be used to specify the credentials to use. The format is of the form:

```netrc
machine my-proxy.example.com
login bobby
password tAble5

```

Note that `my-proxy.example.com` should be the host from the URL (and not the entire URL).
