# Profiler Guide

Attention:

The Profiler is currently an Experimental feature in Lakebridge. For any feedback and/or issues, feel free to reach out via Github issues.

## Overview[​](#overview "Direct link to Overview")

The **Lakebridge Profiler** is designed to extract and analyze metadata from database systems, providing insights into your source environment. The profiler helps you understand system configurations, resource utilization, query patterns, and performance metrics to aid in migration planning.

Key capabilities:

* **Database Metadata Extraction**: Captures schema information, table structures, and object definitions
* **Performance Analytics**: Collects query execution metrics and resource utilization data
* **Workload Analysis**: Profiles active queries and identifies optimization opportunities

Prerequisites

Each system will have different prerequisites either for connection, or metrics collected. Please refer to the details for each system following the links below.

## Supported Source Systems[​](#supported-source-systems "Direct link to Supported Source Systems")

| Source Platform                                         | Configuration Status |
| ------------------------------------------------------- | -------------------- |
| [Azure Synapse](./synapse)                              | ✅                   |
| [Teradata](./teradata)                                  | ✅                   |
| [Snowflake](./snowflake)                                | ✅                   |
| [Microsoft SQL Server](./mssql)                         | ✅                   |
| [Legacy Synapse (Dedicated SQL Pool)](./legacy_synapse) | ✅                   |
| [Oracle](./oracle)                                      | ✅                   |
| [Google BigQuery](./bigquery)                           | ✅                   |
| [Amazon Redshift](./redshift)                           | ✅                   |

## Configure Profiler[​](#configure-profiler "Direct link to Configure Profiler")

Before running the profiler, you need to configure the connection details for your source system.

Execute the following command to configure the profiler, which will prompt you to select the source system and provide connection details specific to that source:

```bash
databricks labs lakebridge configure-database-profiler

```

## Execute Profiler[​](#execute-profiler "Direct link to Execute Profiler")

Once configured, run the profiler to extract metadata and performance metrics from your source system:

```bash
databricks labs lakebridge execute-database-profiler --help

```

output:

```console
Profile the source system database

Usage:
  databricks labs lakebridge execute-database-profiler [flags]

Flags:
  -h, --help                 help for execute-database-profiler
      --source-tech string   (Optional) The technology/platform of the sources to Profile

Global Flags:
      --debug            enable debug logging
  -o, --output type      output type: text or json (default text)
  -p, --profile string   ~/.databrickscfg profile
  -t, --target string    bundle target to use (if applicable)

```

The profiler will:

1. Connect to your source system using the configured credentials
2. Execute the profiling pipeline to extract metadata and metrics
3. Store the results as a natively compressed, directly queryable DuckDB `.db` file

tip

The profiler can be run multiple times to capture different time periods or updated configurations. Each execution will create a timestamped snapshot of your source environment.

## Extract Metadata[​](#extract-metadata "Direct link to Extract Metadata")

Every profiler run writes a `profiler_run_metadata` table into the DuckDB extract at the end of the run to provide run-level metadata for downstream consumption. Failed runs still get a row when possible, so consumers can see which steps failed without parsing logs.

| Column               | Type          | Description                                                                                                              |
| -------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `source_system`      | `VARCHAR`     | Originating platform (for example `mssql`, `snowflake`)                                                                  |
| `variant`            | `VARCHAR`     | Pipeline variant the source was profiled with (for example `single_db`, `multi_db`); `NULL` for sources without variants |
| `pipeline_name`      | `VARCHAR`     | Name of the pipeline configuration that produced the extract                                                             |
| `pipeline_version`   | `VARCHAR`     | Version of the pipeline configuration that produced the extract                                                          |
| `lakebridge_version` | `VARCHAR`     | Lakebridge version that produced the extract                                                                             |
| `python_version`     | `VARCHAR`     | Python version of the environment that ran the profiler                                                                  |
| `operating_system`   | `VARCHAR`     | Operating system, version and architecture of the machine that ran the profiler                                          |
| `status`             | `VARCHAR`     | Run summary: `COMPLETE`, `COMPLETE_WITH_ABSENCES`, or `FAILED`                                                           |
| `results`            | `VARCHAR`     | JSON array of per-step outcomes (`step_name`, `status`, `error_message`)                                                 |
| `generated_at`       | `TIMESTAMPTZ` | UTC timestamp when the metadata row was written                                                                          |
