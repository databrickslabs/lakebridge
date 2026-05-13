# Microsoft SSIS to Databricks

## Conversion Information[​](#conversion-information "Direct link to Conversion Information")

* **Transpiler:** BladeBridge
* **Available target:** Databricks notebooks (experimental)

### Supported SSIS Versions[​](#supported-ssis-versions "Direct link to Supported SSIS Versions")

* SQL Server 2012, 2014, 2016, 2017, 2019, 2022
* Azure Data Factory SSIS Integration Runtime

### Input Requirements[​](#input-requirements "Direct link to Input Requirements")

Export your SSIS packages as DTSX files:

1. **Solution Export:** Export from Visual Studio / SQL Server Data Tools (SSDT)
2. **File System Packages:** Direct DTSX file access
3. **SSISDB Export:** Extract from SSIS catalog

```sql
-- Extract package from SSISDB catalog
DECLARE @packageData VARBINARY(MAX)
SELECT @packageData = [packagedata]
FROM [SSISDB].[catalog].[packages]
WHERE [name] = 'YourPackageName'
-- Save to file system for conversion

```

***

## Running the Conversion[​](#running-the-conversion "Direct link to Running the Conversion")

```bash
databricks labs lakebridge transpile \
  --source-dialect ssis \
  --input-source /path/to/ssis/packages \
  --output-folder /output/sparksql \
  --target-technology sparksql

```

The transpiler recursively scans the input directory for `.dtsx` files and generates Databricks notebook equivalents in the output folder.

Script Component Limitations

SSIS **Script Task** and **Script Component** contain C# or VB.NET code bodies that cannot be automatically converted. The converter preserves the logic structure, but the actual implementation must be rewritten in Python. See [Supported Components](/lakebridge/docs/transpile/source_systems/ssis/supported_components.md#control-flow-orchestration---unsupported) for details.

***

## What Gets Converted[​](#what-gets-converted "Direct link to What Gets Converted")

| SSIS Concept        | Databricks Equivalent                        |
| ------------------- | -------------------------------------------- |
| Control Flow Tasks  | Notebook cells / `dbutils.notebook.run()`    |
| Data Flow Task      | Spark SQL temp views                         |
| Variables           | Python variables                             |
| SSIS Expressions    | Python f-strings / `spark.sql()` calls       |
| Connection Managers | JDBC `spark.read.format("jdbc")`             |
| ForEach Loop        | Wildcard file reads with `input_file_name()` |
| Script Task         | Python with `dbutils`                        |

For the full list of supported and unsupported components, see [SSIS Supported Components](/lakebridge/docs/transpile/source_systems/ssis/supported_components.md).

For conversion examples with before/after code, see [SSIS Conversion Examples](/lakebridge/docs/transpile/source_systems/ssis/examples.md).

***

## Next Steps[​](#next-steps "Direct link to Next Steps")

1. Export SSIS packages to DTSX files
2. Run conversion (command above)
3. Review generated notebooks for conversion warnings
4. Configure Databricks secrets for connection strings
5. Test with sample data in Databricks
6. Deploy workflows to production

For more information, see:

* [SSIS Supported Components](/lakebridge/docs/transpile/source_systems/ssis/supported_components.md)
* [SSIS Conversion Examples](/lakebridge/docs/transpile/source_systems/ssis/examples.md)
* [BladeBridge Configuration](/lakebridge/docs/transpile/pluggable_transpilers/bladebridge/bladebridge_configuration.md)
