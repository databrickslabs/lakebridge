# Reconcile Guide

Lakebridge Reconcile validates data fidelity after migration by comparing your source system against the Databricks target. It identifies discrepancies at the row, column, and schema level.

## What it does[​](#what-it-does "Direct link to What it does")

| Report type | What is compared                       | When to use                                         |
| ----------- | -------------------------------------- | --------------------------------------------------- |
| `schema`    | Column names and data types            | Verify DDL migration is correct                     |
| `row`       | Hash of each row (no join key needed)  | Quick row-level check when there is no primary key  |
| `data`      | Row and column values via join columns | Full fidelity check with per-column mismatch detail |
| `all`       | Both `data` + `schema`                 | Complete validation                                 |

## Supported Source Systems[​](#supported-source-systems "Direct link to Supported Source Systems")

| Source     | Schema | Row                                     | Data                                    | All                                     |
| ---------- | ------ | --------------------------------------- | --------------------------------------- | --------------------------------------- |
| Oracle     | Yes    | Yes                                     | Yes                                     | Yes                                     |
| Snowflake  | Yes    | Yes                                     | Yes                                     | Yes                                     |
| SQL Server | Yes    | Yes                                     | Yes                                     | Yes                                     |
| Redshift   | Yes    | Yes                                     | Yes                                     | Yes                                     |
| Teradata   | Yes    | Yes [1](#user-content-fn-teradata-hash) | Yes [1](#user-content-fn-teradata-hash) | Yes [1](#user-content-fn-teradata-hash) |
| Databricks | Yes    | Yes                                     | Yes                                     | Yes                                     |

***

## Setup[​](#setup "Direct link to Setup")

### Step 1: Setup the source connection[​](#step-1-setup-the-source-connection "Direct link to Step 1: Setup the source connection")

Follow the official Databricks docs to:

* [Create a connection](https://docs.databricks.com/aws/en/query-federation/remote-queries#create-a-connection)
* [Grant connection access](https://docs.databricks.com/aws/en/query-federation/remote-queries#grant-connection-access)
* [Enable Databricks preview](https://docs.databricks.com/aws/en/admin/workspace-settings/manage-previews#-manage-workspace-level-previews) of `remote_query` feature

note

You do not have to create a foreign catalog.

### Step 2: Run `configure-reconcile`[​](#step-2-run-configure-reconcile "Direct link to step-2-run-configure-reconcile")

If you haven't already, complete the initial setup:

```bash
databricks labs lakebridge configure-reconcile

```

This sets up Lakebridge workspace resources. See [Installation → Configure Reconcile](/lakebridge/docs/installation.md#configure-reconcile) for details.

### Config file[​](#config-file "Direct link to Config file")

A reconcile config file is created under the path:

```text
<USER_WORKSPACE_HOME>/.lakebridge/recon_config_<SOURCE>_<UNITY_CATALOG_CONNECTION_NAME_OR_CATALOG>_<REPORT_TYPE>.json

```

note

For `UNITY_CATALOG_CONNECTION_NAME_OR_CATALOG`: if the source is databricks then source catalog name is used else connection name is used

Examples:

| source\_type | connection\_name\_or\_catalog | report\_type | file\_name                                 |
| ------------ | ----------------------------- | ------------ | ------------------------------------------ |
| databricks   | tpch                          | all          | recon\_config\_databricks\_tpch\_all.json  |
| source1      | conn1                         | row          | recon\_config\_source1\_conn1\_row\.json   |
| source2      | conn2                         | schema       | recon\_config\_source2\_conn2\_schema.json |

See [Configuration Reference](/lakebridge/docs/reconcile/configuration.md) for the full schema and examples.

### Required permissions[​](#required-permissions "Direct link to Required permissions")

The User configuring reconcile must have permission to:

* Create Data Warehouses
* Create Compute Clusters
* `USE CONNECTION` on the source connection
* `USE CATALOG` and `CREATE SCHEMA` on the target catalog
* `CREATE VOLUME` if using a pre-existing schema on a serverless cluster

### Serverless cluster support[​](#serverless-cluster-support "Direct link to Serverless cluster support")

Reconcile automatically detects the cluster type and optimizes intermediate data persistence accordingly:

* **On Serverless clusters**: Reconcile uses Unity Catalog volumes for intermediate data persistence
* **On Standard clusters**: Reconcile uses DataFrame caching for better performance

note

* On serverless clusters, the configured volume (from `metadata_config.volume`) is automatically used
* The volume must be created in the metadata catalog and schema specified in your `ReconcileMetadataConfig`
* Ensure you have the necessary permissions to write to the volume on serverless clusters

Reconcile automatically adapts to the cluster type:

* **Serverless clusters:** Uses Unity Catalog volumes for intermediate data persistence (`metadata_config.volume`)
* **Standard clusters:** Uses DataFrame caching

***

## Run[​](#run "Direct link to Run")

See [Running Reconcile](/lakebridge/docs/reconcile/running.md) for CLI execution, notebook usage, and automation.

<!-- -->

## Footnotes[​](#footnote-label "Direct link to Footnotes")

1. Teradata has no portable cryptographic hash in pure SQL, so row-hash report types (`row`, `data`, `all`) require a user-installed hash UDF on the source and an explicit `hash_expression_overrides.source` entry on the recon config. See [Hash Expression](/lakebridge/docs/reconcile/configuration.md#hash-expression) for wiring. [↩](#user-content-fnref-teradata-hash) [↩2](#user-content-fnref-teradata-hash-2) [↩3](#user-content-fnref-teradata-hash-3)
