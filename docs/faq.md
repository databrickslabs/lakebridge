# FAQs

## General[​](#general "Direct link to General")

### What is Lakebridge?[​](#what-is-lakebridge "Direct link to What is Lakebridge?")

Lakebridge is a Databricks toolkit for migrating data workloads to Databricks. It covers three phases:

1. **Assessment** — analyze your existing SQL or ETL environment and code to understand complexity and effort
2. **Transpilation** — convert source SQL or ETL code to Databricks-compatible SQL, PySpark, or notebooks
3. **Reconciliation** — validate that the migrated data matches the source

### Do I need to run all three phases?[​](#do-i-need-to-run-all-three-phases "Direct link to Do I need to run all three phases?")

No. Use the phases you need:

* **Assess only** if you are scoping a migration before committing to it
* **Transpile only** if you are migrating code and do not need pre-migration analysis or post-migration validation
* **Reconcile only** if you have already migrated data and want to validate row/column fidelity

### Which source systems are supported?[​](#which-source-systems-are-supported "Direct link to Which source systems are supported?")

See the [supported dialects table](/lakebridge/docs/transpile.md#supported-dialects) for a full list. In summary:

* **SQL sources:** SQL Server, Snowflake, Azure Synapse Analytics, Oracle, Teradata, Netezza, Redshift, MySQL, PostgreSQL
* **ETL sources:** DataStage, SSIS, others
* **Other:** Airflow, Python, Scala (via Switch)

***

## Assessment[​](#assessment "Direct link to Assessment")

### Profiler vs Analyzer — which should I use?[​](#profiler-vs-analyzer--which-should-i-use "Direct link to Profiler vs Analyzer — which should I use?")

See [Which Tool Do I Use? — Profiler vs Analyzer](/lakebridge/docs/choosing_tools.md#assessment-profiler-vs-analyzer).

Short answer: use the Profiler for executive-level scoping; use the Analyzer for per-file migration planning.

### How do I interpret complexity scores?[​](#how-do-i-interpret-complexity-scores "Direct link to How do I interpret complexity scores?")

The Analyzer classifies each SQL object as LOW, MEDIUM, HIGH, or VERY HIGH. As a rule of thumb:

* **LOW / MEDIUM** objects typically transpile cleanly with no manual review required
* **HIGH** objects should be reviewed after transpilation for any warnings
* **VERY HIGH** objects (>50% of total) suggest you should plan for 2+ manual review passes before deploying

See [Complexity Scoring](/lakebridge/docs/assessment/analyzer/complexity_scoring.md) for the exact thresholds by source system.

### Should I run the SQL Splitter before the Analyzer?[​](#should-i-run-the-sql-splitter-before-the-analyzer "Direct link to Should I run the SQL Splitter before the Analyzer?")

Yes. The Analyzer works at the individual-object level. If your SQL files mix multiple objects (stored procedures, tables, views), split them first with the [SQL Splitter](/lakebridge/docs/sql_splitter.md) so the Analyzer produces per-object granularity.

***

## Transpilation[​](#transpilation "Direct link to Transpilation")

### Morpheus[​](#morpheus "Direct link to Morpheus")

#### Which dialects does Morpheus support?[​](#which-dialects-does-morpheus-support "Direct link to Which dialects does Morpheus support?")

Morpheus supports `mssql` (SQL Server, Azure SQL, RDS for SQL Server), `snowflake` (including dbt repointing), and `synapse` (Azure Synapse Analytics dedicated SQL pools). It does **not** support Redshift, Oracle, or ETL platforms.

For other dialects, use [BladeBridge](/lakebridge/docs/transpile/pluggable_transpilers/bladebridge.md) or [Switch](/lakebridge/docs/transpile/pluggable_transpilers/switch.md).

#### What is the difference between an error and a warning in Morpheus output?[​](#what-is-the-difference-between-an-error-and-a-warning-in-morpheus-output "Direct link to What is the difference between an error and a warning in Morpheus output?")

* **Error** — Morpheus knows that the transpiled output cannot be guaranteed to produce equivalent results. Manual fix required before deploying.
* **Warning** — Morpheus could not confirm equivalence but the output *may* still be correct (a conservative false-negative). Review the flagged section and test against source data.

A file transpiled with **no errors and no warnings** carries a full correctness guarantee.

#### My file transpiled with warnings but the output looks correct. Is it safe to use?[​](#my-file-transpiled-with-warnings-but-the-output-looks-correct-is-it-safe-to-use "Direct link to My file transpiled with warnings but the output looks correct. Is it safe to use?")

Possibly. Morpheus is conservative — it warns when it cannot *guarantee* correctness, even if the actual output is correct. Test the transpiled file against your source data. If the results match, it is safe to deploy.

#### What should I do when a file fails to parse?[​](#what-should-i-do-when-a-file-fails-to-parse "Direct link to What should I do when a file fails to parse?")

Parsing errors are very rare and indicate either malformed input SQL or a gap in the Morpheus ANTLR grammar. Verify that the input file is valid SQL. If it is, file a bug at [GitHub Issues](https://github.com/databrickslabs/lakebridge/issues).

### BladeBridge[​](#bladebridge "Direct link to BladeBridge")

#### Which dialects does BladeBridge support?[​](#which-dialects-does-bladebridge-support "Direct link to Which dialects does BladeBridge support?")

BladeBridge supports SQL dialects (Oracle, Teradata, Netezza, SQL Server, Synapse, Redshift) and ETL platforms (DataStage, SSIS). See the [supported dialects table](/lakebridge/docs/transpile.md#supported-dialects).

#### What is the `target-tech` parameter?[​](#what-is-the-target-tech-parameter "Direct link to what-is-the-target-tech-parameter")

`target-tech` controls the output format: `SQL` (Databricks SQL), `SPARKSQL` (SparkSQL notebooks), or `PYSPARK` (PySpark notebooks). It only applies to ETL sources — SQL dialects always output Databricks SQL.

Available options per ETL dialect:

* `datastage`: `SPARKSQL` or `PYSPARK`
* `ssis`: `SPARKSQL` only (PYSPARK not available)

#### How do I customize BladeBridge output?[​](#how-do-i-customize-bladebridge-output "Direct link to How do I customize BladeBridge output?")

Use a custom JSON override file. Pass it via `--overrides-file` or set it during `install-transpile`. See [BladeBridge Configuration](/lakebridge/docs/transpile/pluggable_transpilers/bladebridge/bladebridge_configuration.md) for the full config reference.

### Switch[​](#switch "Direct link to Switch")

#### Can Switch support my source system if it's not in the built-in list?[​](#can-switch-support-my-source-system-if-its-not-in-the-built-in-list "Direct link to Can Switch support my source system if it's not in the built-in list?")

Yes. Switch uses LLMs to convert arbitrary source formats through custom YAML prompts. If your dialect is not in the [built-in list](/lakebridge/docs/transpile/pluggable_transpilers/switch.md#source-format-support), create a custom prompt YAML:

* Start with a similar built-in dialect's YAML as a template
* Add examples specific to your source dialect
* Reference [SQLGlot dialects](https://github.com/tobymao/sqlglot/tree/main/sqlglot/dialects) for dialect-specific patterns

#### My Switch files show status "Not converted". What does that mean?[​](#my-switch-files-show-status-not-converted-what-does-that-mean "Direct link to My Switch files show status \"Not converted\". What does that mean?")

The file exceeded the `token_count_threshold` and was skipped. Solutions:

* Split the file into smaller parts
* Increase `token_count_threshold` in `switch_config.yml` if your model supports it

#### My Switch files show "Converted with errors". How do I fix them?[​](#my-switch-files-show-converted-with-errors-how-do-i-fix-them "Direct link to My Switch files show \"Converted with errors\". How do I fix them?")

The LLM converted the file but the output has syntax errors. Options:

1. Review the `error_details` column in the conversion result table
2. Increase `max_fix_attempts` in `switch_config.yml` for more automatic correction attempts
3. Fix errors manually in the output notebook

#### Switch exported some files but they weren't written to the output directory.[​](#switch-exported-some-files-but-they-werent-written-to-the-output-directory "Direct link to Switch exported some files but they weren't written to the output directory.")

Check the `export_error` column in the result table. Common causes:

* **Size limit:** Notebooks >10MB cannot be written. Split the converted content manually.
* **Permissions:** Verify your user has write access to the workspace output path.
* **Invalid path:** Output paths must start with `/Workspace/`.

***

## Reconciliation[​](#reconciliation "Direct link to Reconciliation")

### Commonly used custom transformations[​](#commonly-used-custom-transformations "Direct link to Commonly used custom transformations")

| source\_type | data\_type     | source\_transformation                                                          | target\_transformation                                   | comments                           |
| ------------ | -------------- | ------------------------------------------------------------------------------- | -------------------------------------------------------- | ---------------------------------- |
| Oracle       | number(10,5)   | ```sql
"trim(to_char(coalesce(col_name,0.0), '99990.99999'))"

```                 | ```sql
"cast(coalesce(col_name,0.0) as decimal(10,5))"

``` | Adjust precision/scale as needed   |
| Snowflake    | array          | ```sql
"array_to_string(array_compact(col_name),',')"

```                         | ```sql
"concat_ws(',', col_name)"

```                      | Removes undefined during migration |
| Snowflake    | array          | ```sql
"array_to_string(array_sort(array_compact(col_name), true, true),',')"

``` | ```sql
"concat_ws(',', col_name)"

```                      | Removes undefined and sorts        |
| Snowflake    | timestamp\_ntz | ```sql
"date_part(epoch_second,col_name)"

```                                     | ```sql
"unix_timestamp(col_name)"

```                      | Convert timestamp\_ntz to epoch    |

### How do I reconcile tables when column names differ between source and target?[​](#how-do-i-reconcile-tables-when-column-names-differ-between-source-and-target "Direct link to How do I reconcile tables when column names differ between source and target?")

Use `column_mapping` in your table config:

```json
"column_mapping": [
  { "source_name": "dept_id", "target_name": "department_id" }
]

```

See [Configuration Reference — Column Mapping](/lakebridge/docs/reconcile/configuration.md#column-mapping).

### How do I exclude specific columns from reconciliation?[​](#how-do-i-exclude-specific-columns-from-reconciliation "Direct link to How do I exclude specific columns from reconciliation?")

Use `drop_columns`:

```json
"drop_columns": ["audit_timestamp", "etl_load_date"]

```

***

## Troubleshooting[​](#troubleshooting "Direct link to Troubleshooting")

### Install Databricks CLI on Linux without brew[​](#install-databricks-cli-on-linux-without-brew "Direct link to Install Databricks CLI on Linux without brew")

```bash
#!/usr/bin/env bash
apt update && apt install -y curl sudo unzip
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/v0.299.0/install.sh | sudo sh

```

### The `configure-reconcile` command fails because I can't create SQL warehouses or clusters.[​](#the-configure-reconcile-command-fails-because-i-cant-create-sql-warehouses-or-clusters "Direct link to the-configure-reconcile-command-fails-because-i-cant-create-sql-warehouses-or-clusters")

Add your existing warehouse or cluster ID to your Databricks CLI profile:

```text
[profile-name]
host         = <your-workspace-url>
warehouse_id = <your-warehouse-id>
cluster_id   = <your-cluster-id>

```

### Transpilation produces output files with headers but no SQL.[​](#transpilation-produces-output-files-with-headers-but-no-sql "Direct link to Transpilation produces output files with headers but no SQL.")

This means the entire file failed to parse. Check that:

1. The input file is valid SQL for the declared `--source-dialect`
2. The file is not empty
3. If using Morpheus, the input SQL matches a supported dialect (`mssql`, `snowflake`, or `synapse`)

### The `install-transpile` command fails with a download error.[​](#the-install-transpile-command-fails-with-a-download-error "Direct link to the-install-transpile-command-fails-with-a-download-error")

Lakebridge downloads transpiler components from GitHub, Maven Central, and PyPI. If you are in a restricted network:

1. Whitelist the required endpoints (see [Installation — Network access](/lakebridge/docs/installation.md#network-access-proxies-and-mirrors))
2. Or set up a private artifact mirror (Artifactory, Nexus) and configure it as the download source

### How do I report a bug or request a feature?[​](#how-do-i-report-a-bug-or-request-a-feature "Direct link to How do I report a bug or request a feature?")

Open an issue at [github.com/databrickslabs/lakebridge/issues](https://github.com/databrickslabs/lakebridge/issues). For Switch-specific issues, include the LLM model name and a sample of the input that failed to convert.
