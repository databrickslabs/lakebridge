# Getting Started with Lakebridge

This guide walks you through a complete end-to-end migration using **SQL Server as the source** and **Databricks SQL as the target**. You will assess your SQL code, transpile it, and validate the output — in about 15 minutes.

**Prerequisites:** Lakebridge must already be installed. If not, see [Installation](/lakebridge/docs/installation.md) first.

***

## *(Optional)* Step 1: Split Your SQL Files[​](#optional-step-1-split-your-sql-files "Direct link to optional-step-1-split-your-sql-files")

If your SQL code lives in monolithic files that contain multiple objects (stored procedures, tables, views, functions), it is recommended to split them first.

```bash
./sqlsplit -d /path/to/your/sql -o /path/to/split/output

```

* `-d` accepts a directory of `.sql` files
* `-o` must be a directory that already exists
* Output is organized into subfolders: `/PROCEDURE`, `/FUNCTION`, `/TABLE`, `/VIEW`

See [SQL Splitter](/lakebridge/docs/sql_splitter.md) for full usage and download.

tip

Splitting first gives the Analyzer more granular, per-object results and gives the transpiler a cleaner input.

***

## *(Optional)* Step 2: Assess Your Code[​](#optional-step-2-assess-your-code "Direct link to optional-step-2-assess-your-code")

Run the Analyzer on the (optionally split) files to understand complexity and identify patterns the transpiler may need help with.

```bash
databricks labs lakebridge analyze

```

When prompted, enter the following details:

* **Input path:** `/path/to/split/output`
* **Source dialect:** `mssql`
* **Report file:** `/path/to/transpiled/output/output.xlsx`
* **Source technology:** Select the number corresponding to `MS SQL Server`

The Analyzer produces a complexity report saved as an Excel file (`.xlsx`). Objects flagged as `HIGH` or `VERY HIGH` complexity may need manual review after transpilation.

See [Assessment Guide](/lakebridge/docs/assessment.md) for more details.

***

## Step 3: Configure Transpilation[​](#step-3-configure-transpilation "Direct link to Step 3: Configure Transpilation")

During `install-transpile` (covered in [Installation](/lakebridge/docs/installation.md#install-transpile)), you are prompted for your migration parameters. For this *Getting Started* guide, the values to use are:

* **Source dialect:** Select the number corresponding to `mssql`
* **Input path:** `/path/to/split/output`
* **Output directory:** `/path/to/transpiled/output`
* **Transpiler:** Select the number corresponding to `Morpheus` (both Morpheus and BladeBridge support SQL Server, but this guide assumes Morpheus)

If you need to change these settings, re-run `install-transpile`.

Morpheus is the recommended transpiler for SQL Server migrations — it provides strong correctness guarantees and warns you when it cannot fully guarantee equivalence.

See [Which transpiler should I use?](/lakebridge/docs/choosing_tools.md#transpiler-bladebridge-vs-morpheus-vs-switch) if you are migrating from a different source.

***

## Step 4: Transpile[​](#step-4-transpile "Direct link to Step 4: Transpile")

Run the transpilation (if params already configured in previous step):

```bash
databricks labs lakebridge transpile

```

Or pass all parameters inline:

```bash
databricks labs lakebridge transpile \
  --source-dialect mssql \
  --input-source /path/to/split/output \
  --output-folder /path/to/transpiled/output

```

**Reading the output:**

In the command output, you will see a final summary with all files converted and whether any errors were found.

You may also find inline comments in the output files prefixed with "FIXME" that you'll need to review.

Files with errors still contain as much translated output as possible. Review the flagged positions and fix manually.

***

## Step 5: Reconcile[​](#step-5-reconcile "Direct link to Step 5: Reconcile")

Prerequisite

This step requires `configure-reconcile` to have been completed during installation. If you skipped it, run it now — see [Installation → Configure Reconcile](/lakebridge/docs/installation.md#configure-reconcile).

After deploying your transpiled SQL to Databricks, run the Reconciler to validate that the data output matches the source.

```bash
databricks labs lakebridge reconcile

```

The Reconciler compares row counts, schema, and data values between your source system and the Databricks target. Results are written to a dashboard in your workspace.

See [Reconcile Guide](/lakebridge/docs/reconcile.md) for full configuration details.

***

## What's Next[​](#whats-next "Direct link to What's Next")

| Next step                         | Guide                                                                                                                                                                                                                         |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Understand transpiler differences | [Which Tool Do I Use?](/lakebridge/docs/choosing_tools.md)                                                                                                                                                                    |
| Migrate ETL workloads             | [Source Systems](/lakebridge/docs/transpile/source_systems.md)                                                                                                                                                                |
| Customize transpiler output       | [BladeBridge Configuration](/lakebridge/docs/transpile/pluggable_transpilers/bladebridge/bladebridge_configuration.md), [Switch Configuration](/lakebridge/docs/transpile/pluggable_transpilers/switch/customizing_switch.md) |
