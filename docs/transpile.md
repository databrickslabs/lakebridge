# Transpile Guide

Lakebridge comes with three transpilation plugins: [Morpheus](/lakebridge/docs/transpile/pluggable_transpilers/morpheus.md), [BladeBridge](/lakebridge/docs/transpile/pluggable_transpilers/bladebridge.md), and [Switch](/lakebridge/docs/transpile/pluggable_transpilers/switch.md). Not sure which to use? See [Which Tool Do I Use?](/lakebridge/docs/choosing_tools.md#transpiler-bladebridge-vs-morpheus-vs-switch).

## Transpiler Selection Guide[​](#transpiler-selection-guide "Direct link to Transpiler Selection Guide")

Lakebridge offers both deterministic and LLM-powered transpilers, each optimized for different conversion scenarios.

### Deterministic Conversion (BladeBridge & Morpheus)[​](#deterministic-conversion-bladebridge--morpheus "Direct link to Deterministic Conversion (BladeBridge & Morpheus)")

Deterministic transpilers excel in scenarios requiring consistency and speed:

* **Deterministic output with guaranteed syntax equivalence** — Every conversion produces the same predictable result
* **High-volume batch processing** — Efficiently handle thousands of files without API rate limits
* **Fast local execution without API dependencies** — Sub-minute processing with no external service calls
* **Production-grade SQL aligned with Databricks SQL evolution** — Leverages SQL Scripting, Stored Procedures, and latest DBSQL features

### LLM-Powered Conversion (Switch)[​](#llm-powered-conversion-switch "Direct link to LLM-Powered Conversion (Switch)")

Switch is best suited for scenarios requiring semantic understanding and flexibility:

* **Complex logic requiring contextual understanding** — Stored procedures and business logic where intent matters more than syntax
* **Source formats not covered by deterministic transpilers** — Any SQL dialect or programming language through custom prompts
* **Extensible conversion through custom YAML prompts** — Adapt to proprietary or uncommon source formats
* **Python notebook output for SQL beyond ANSI SQL/PSM standards** — Complex transformations that benefit from procedural code

***

## Supported dialects[​](#supported-dialects "Direct link to Supported dialects")

| Source system type | Source Technology | Source System                                                                                             | BladeBridge             | Morpheus | Switch (Experimental) |
| ------------------ | ----------------- | --------------------------------------------------------------------------------------------------------- | ----------------------- | -------- | --------------------- |
| SQL                | `mssql`           | MMicrosoft SQL Server, Azure SQL Database, Azure SQL Managed Instance, Amazon RDS for SQL Server          | DBSQL                   | DBSQL    | SparkSql              |
|                    | `mysql`           | MySQL, MariaDB, and MySQL-compatible services (including Amazon Aurora MySQL, RDS, Google Cloud SQL)      |                         |          | SparkSql              |
|                    | `netezza`         | IBM Netezza                                                                                               | DBSQL                   |          | SparkSql              |
|                    | `oracle`          | Oracle Database, Oracle Exadata, and Oracle-compatible services (including Amazon RDS)                    | DBSQL                   |          | SparkSql              |
|                    | `postgresql`      | PostgreSQL and PostgreSQL-compatible services (including Amazon Aurora PostgreSQL, RDS, Google Cloud SQL) |                         |          | SparkSql              |
|                    | `redshift`        | Amazon Redshift                                                                                           | DBSQL (experimental)    |          | SparkSql              |
|                    | `snowflake`       | Snowflake (including dbt Repointing)                                                                      |                         | DBSQL    | SparkSql              |
|                    | `synapse`         | Azure Synapse Analytics (dedicated SQL pools)                                                             | DBSQL                   | DBSQL    | SparkSql              |
|                    | `teradata`        | Teradata                                                                                                  | DBSQL                   |          | SparkSql              |
| ETL                | `datastage`       | IBM DataStage                                                                                             | SparkSql, PySpark       |          | SDP                   |
|                    | `ssis`            | SSIS                                                                                                      | SparkSql (experimental) |          | SDP                   |
| Orchestration      |                   | Airflow                                                                                                   |                         |          | Databricks Workflow   |
| Generic            |                   | Python Code                                                                                               |                         |          | Python Notebook       |
|                    |                   | Scala Code                                                                                                |                         |          | Python Notebook       |

## Execution Pre-Set Up[​](#execution-pre-set-up "Direct link to Execution Pre-Set Up")

When you run `install-transpile`, you will be prompted for settings to use when transpiling your sources. You can choose to provide these at the time of installation, or to provide them later as arguments when transpiling.

The `transpile` command will trigger the conversion of the specified code. These settings provided during `install-transpile` can be overridden (or provided if unavailable) using the command-line options:

* `input-source`: The local filesystem path to the sources that should be transpiled. This must be provided if not set during `install-transpile`.
* `output-folder`: The local filesystem path where converted code will be written. This must be provided if not set during `install-transpile`.
* `source-dialect`: Dialect name (ex: snowflake, oracle, datastage, etc). This must be provided if not set during `install-transpile`.
* `overrides-file`: An optional local path to a JSON file containing custom overrides for the transpilation process, if the underlying transpiler supports this. (Refer to [this documentation](/lakebridge/docs/transpile/pluggable_transpilers/bladebridge/bladebridge_configuration.md) for more details on custom overrides.)
* `target-technology`: The target technology to use for conversion output. This must be provided if not set during `install-transpile` and the underlying transpiler requires it for the source dialect in use.
* `error-file-path`: The path to the file where a log of conversion errors will be stored. If not provided here or during `install-transpile` no error log will be written.
* `skip-validation`: Whether the transpiler will skip the validation of transpiled SQL sources. If not provided here or during `install-transpile` validation will be attempted by default.
* `catalog-name`: The name of the catalog in Databricks to use when validating transpiled SQL sources. If not specified, `remorph` will be used as the default catalog.
* `schema-name`: The name of the schema in Databricks to use when validating transpiled SQL sources. If not specified, `transpiler` will be used as the default schema.
* `transpiler-config-path`: This path of the configuration file for the transpiler to use for conversion. This is normally inferred from the source dialect or chosen during `install-transpile` if multiple transpilers support the source dialect.

## Verify Installation[​](#verify-installation "Direct link to Verify Installation")

Verify the successful installation by executing the provided command; confirmation of a successful installation is indicated when the displayed output aligns with the example screenshot provided:

Command:

```bash
databricks labs lakebridge transpile --help

```

Should output:

```console
Transpile SQL/ETL sources to Databricks-compatible code

Usage:
  databricks labs lakebridge transpile [flags]

Flags:
      --catalog-name name             (Optional) Catalog name, only used when validating converted code
      --error-file-path path          (Optional) Local path where a log of conversion errors (if any) will be written
  -h, --help                          help for transpile
      --input-source path             (Optional) Local path of the sources to be convert
      --output-folder path            (Optional) Local path where converted code will be written
      --schema-name name              (Optional) Schema name, only used when validating converted code
      --skip-validation string        (Optional) Whether to skip validating the output ('true') after conversion or not ('false')
      --source-dialect string         (Optional) The source dialect to use when performing conversion
      --transpiler-config-path path   (Optional) Local path to the configuration file of the transpiler to use for conversion

Global Flags:
      --debug            enable debug logging
  -o, --output type      output type: text or json (default text)
  -p, --profile string   ~/.databrickscfg profile
  -t, --target string    bundle target to use (if applicable)

```

## Execution[​](#execution "Direct link to Execution")

Execute the below command to initialize the transpile process passing the arguments to the command directly in the call.

```bash
databricks labs lakebridge transpile --transpiler-config-path <absolute-path> --input-source <absolute-path> --source-dialect <snowflake> --output-folder <absolute-path> --skip-validation <True|False> --catalog-name <catalog name> --schema-name <schema name>

```

![transpile-run](/lakebridge/img/transpile-run.gif)

<br />

<br />

If you have configured all the required inputs at installation time, you can simply run:

```bash
databricks labs lakebridge transpile

```
