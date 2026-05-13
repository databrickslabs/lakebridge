# Exporting Legacy Metadata

Analyzer expects all the legacy code to be exported into a folder accessible by it.<br />For SQL code exporting, most times a database export can be used to export object definitions (tables, views procedures, functions etc...) in bulk. Ideally, each SQL file should contain a single artifact.<br />

All the major ETL platforms provide some kind of export of their code repositories. Typically this is done into XML or JSON formats which can be used to restore the environment. Here is a short guide for how to export metadata from various platforms:

## Microsoft SQL Server[​](#microsoft-sql-server "Direct link to Microsoft SQL Server")

To extract metadata like Table, View, and Stored Procedures DDLs, you can use Microsoft SQL Server Management Studio (SSMS).

* In Object Explorer, expand the node for the instance containing the database to be scripted.
* Right-click on the database you want to script, and select Tasks > Generate Scripts.
  <!-- -->
  ![sql-server-export-object-explorer](/lakebridge/img/sql-server-export-object-explorer.png)
* On the `Choose Objects` dialog page, select either the entire database or choose the object types to be migrated.
  <!-- -->
  * Note: Select all required object types. Screenshot below is for illustration purposes only.
  ![sql-server-export-choose-objects](/lakebridge/img/sql-server-export-choose-objects.png)
* In the `Set Scripting Options` dialog page, choose `Save as script file` and `one script file per object` as illustrated below.
  <br />
  ![sql-server-export-set-scripting-options](/lakebridge/img/sql-server-export-set-scripting-options.png)

See <https://learn.microsoft.com/en-us/ssms/scripting/generate-and-publish-scripts-wizard> for more details on how to use the Generate Scripts wizard in SSMS.

## Azure Synapse (Dedicated)[​](#azure-synapse-dedicated "Direct link to Azure Synapse (Dedicated)")

Follow the same steps as for Microsoft SQL Server above. The only difference is that you will need to connect to the Synapse Dedicated SQL pool instead of a regular SQL Server instance.

## Azure Synapse (Serverless)[​](#azure-synapse-serverless "Direct link to Azure Synapse (Serverless)")

If you use Synapse Studio and have your SQL code saved in SQL scripts, you can export the files with the [Export-AzSynapseSqlScript PowerShell cmdlet](https://learn.microsoft.com/en-us/powershell/module/az.synapse/export-azsynapsesqlscript?view=azps-14.2.0\&viewFallbackFrom=azps-13.4.0). This method requires [Azure PowerShell modules](https://learn.microsoft.com/en-us/powershell/azure/install-Az-ps?view=azps-0.10.0).<br />

Otherwise, you can use Microsoft SQL Server Management Studio (SSMS) to extract metadata like Table, View, and Stored Procedures DDLs.

* Select “Object Explorer Details” under the View button in the toolbar
  <!-- -->
  ![synapse-objects-explorer-view](/lakebridge/img/synapse-objects-explorer-view.png)
* For each object type, select the required objects to export and right-click on the selection to choose “Script as” > “CREATE To” > “File” as pictured below.
  <br />
  ![synapse-objects-explorer-script-as](/lakebridge/img/synapse-objects-explorer-script-as.png)

## DataStage[​](#datastage "Direct link to DataStage")

* Typically in DataStage the easiest way to export the objects is by using the GUI. However, Datastage has command line utilities to export via CLI.
* Please use the XML format, as both Analyzer and Converter support XML-only Datastage exports

## SSIS[​](#ssis "Direct link to SSIS")

You’ll need to export the DTSX packages. For details on how to obtain it see: <https://docs.microsoft.com/en-us/sql/integration-services/import-export-data/save-and-run-package-sql-server-import-and-export-wizard?view=sql-server-ver15>

In many cases the DTSX packages can also be just copied to the analyzer folder.

## Talend[​](#talend "Direct link to Talend")

To export all jobs in bulk, right click on Job Designs and select "Export Items". In the popup, select "Include All Dependencies"

![talend-export](/lakebridge/img/talend-metadata-extract.png)

Note: while Talend jobs can be exported as a single zip file, when running analyzer or any converter utilities please unzip the file(s). Both the analyzer and converters will look for .item and .properties files in non-zipped folders.

## ODI[​](#odi "Direct link to ODI")

Exporting jobs in ODI is detailed in this document: <https://docs.oracle.com/middleware/1212/odi/ODIDG/export_import.htm#ODIDG578>

## Alteryx[​](#alteryx "Direct link to Alteryx")

Analyzer needs the .yxmd files. These can be obtained by Select File > Export to download your workflow to your local machine in .yxmd format.

## SAP Business Objects Data Services[​](#sap-business-objects-data-services "Direct link to SAP Business Objects Data Services")

Instructions for export can be found in the following articles: <https://help.sap.com/viewer/2d2abbb0fab34071a4c53b7de873241b/4.2.13/en-US/571901366d6d1014b3fc9283b0e91070.html> <https://help.sap.com/viewer/2d2abbb0fab34071a4c53b7de873241b/4.2.13/en-US/5718d4ba6d6d1014b3fc9283b0e91070.html>
