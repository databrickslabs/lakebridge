# Overview

Lakebridge is a comprehensive toolkit designed to help you manage all phases of your SQL migration, from initially surveying your existing landscape through to translation of SQL and final data reconciliation.

1. [*Pre-migration:*](#pre-migration-assessment) Assessing your existing landscape, helping you understand the impact and effort of your migration to Databricks.
2. [*Converting*](#converting-sql-workloads) your SQL-based workloads, using the proven BladeBridge solution or our next-generation transpiler.
3. [*Post-migration:*](#post-migration-reconciliation) Reconciling datasets that you've transferred from an existing warehouse or data lake into Databricks.

## Pre-migration Assessment[​](#pre-migration-assessment "Direct link to Pre-migration Assessment")

The assessment phase is where we analyze your existing SQL workloads and their orchestration, with a view to helping you understand ahead of your migration to Databricks: a) the total cost of ownership (TCO) before you start; b) the complexity and effort of the migration itself. We split this up into two main tasks:

* *Profiling* your existing SQL workloads. The profiler connects to your existing SQL environment and examines its workloads, providing you with a detailed report on their size, complexity and features used. From this we can estimate the savings of running the same workloads once they have been migrated to Databricks.
* *Analyzing* the SQL code, and if necessary its orchestration. This scan provides you with a detailed report on the size and complexity of the code, and identifies potential issues that may arise during migration. This helps you understand the effort that will be required to update your SQL jobs, and if necessary its orchestration, for Databricks.

The key purpose here is to provide you as early as possible with a clear understanding of both the TCO benefits of migrating to Databricks, while also being aware of the effort and potential issues this may involve. Aside from setting expectations, this is crucial for planning and understanding the risks associated with your migration.

For more information on using the assessment tools that we provide, refer to the [Profiler](/lakebridge/docs/assessment/profiler.md) and [Analyzer](/lakebridge/docs/assessment/analyzer.md) documentation.

## Converting SQL Workloads[​](#converting-sql-workloads "Direct link to Converting SQL Workloads")

For migrating your SQL workloads we provide transpilers that can:

* Translate SQL code from a variety of source platforms to Databricks SQL.
* Translate some orchestration and ETL code to Databricks SQL.

Internally, Lakebridge can use three different transpilers:

* *BladeBridge*, a mature transpiler that can handle a wide range of source dialects as well as some ETL/orchestration.
* *Morpheus*, a next-generation transpiler that currently handles a smaller set of dialects, but includes experimental support for dbt.
* *Switch*, an LLM-powered transpiler that uses Large Language Models to convert SQL and other source formats to Databricks notebooks.

For more information on using the transpilers, refer to the [Transpile](/lakebridge/docs/transpile.md) documentation.

## Post-migration Reconciliation[​](#post-migration-reconciliation "Direct link to Post-migration Reconciliation")

During the migration process, datasets are often transferred from the existing source platform into Databricks. Lakebridge's reconciler is designed to help you ensure that the data in Databricks matches that of the source system, bearing in mind that both might be part of live environments.

For more information on using the data reconciliation tools, refer to the [Reconcile](/lakebridge/docs/reconcile.md) documentation.

## Supported sources[​](#supported-sources "Direct link to Supported sources")

| Source system type | Source Technology     | Assessment - Profiler | Assessment - Analyzer | Converter | Reconcile |
| ------------------ | --------------------- | --------------------- | --------------------- | --------- | --------- |
| SQL                | athena                |                       | ✅                    |           |           |
|                    | big query             | ✅                    | ✅                    |           |           |
|                    | db2                   |                       | ✅                    |           |           |
|                    | greenplum             |                       | ✅                    |           |           |
|                    | hive                  |                       | ✅                    |           |           |
|                    | mssql                 | ✅                    | ✅                    | ✅        | ✅        |
|                    | mysql                 |                       | ✅                    | ✅        |           |
|                    | netezza               |                       | ✅                    | ✅        |           |
|                    | oracle                | ✅                    | ✅                    | ✅        | ✅        |
|                    | postgresql            |                       | ✅                    | ✅        |           |
|                    | presto                |                       | ✅                    |           |           |
|                    | redshift              |                       | ✅                    | ✅        | ✅        |
|                    | sap hana (calc views) |                       | ✅                    |           |           |
|                    | snowflake             | ✅                    | ✅                    | ✅        | ✅        |
|                    | synapse               | ✅                    | ✅                    | ✅        | ✅        |
|                    | teradata              |                       | ✅                    | ✅        | ✅        |
|                    | vertica               |                       | ✅                    |           |           |
| ETL                | abinitio              |                       | ✅                    |           |           |
|                    | alteryx               |                       | ✅                    |           |           |
|                    | datastage             |                       | ✅                    | ✅        |           |
|                    | pentahodi             |                       | ✅                    |           |           |
|                    | pig                   |                       | ✅                    |           |           |
|                    | pyspark               |                       | ✅                    |           |           |
|                    | sas                   |                       | ✅                    |           |           |
|                    | sqoop                 |                       | ✅                    |           |           |
|                    | ssis                  |                       | ✅                    | ✅        |           |
|                    | talend                |                       | ✅                    |           |           |
| Orchestration      | airflow               |                       |                       | ✅        |           |
|                    | adf                   |                       | ✅                    |           |           |
|                    | oozie                 |                       | ✅                    |           |           |
| Generic/Other      | cloudera (impala)     |                       | ✅                    |           |           |
|                    | python                |                       | ✅                    | ✅        |           |
|                    | scala                 |                       | ✅                    | ✅        |           |
|                    | spss                  |                       | ✅                    |           |           |
|                    | ssrs                  |                       | ✅                    |           |           |
