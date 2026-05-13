# Synapse Profiler Details

* [Prerequisites](#prerequisites)
* [Configure Connection to Synapse](#configure-connection-to-synapse)

## Prerequisites[​](#prerequisites "Direct link to Prerequisites")

### 1. Download[​](#1-download "Direct link to 1. Download")

* Azure CLI (<https://learn.microsoft.com/en-us/cli/azure/install-azure-cli>)
* ODBC driver for SQL Server (<https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server?view=sql-server-ver17>)
* (Windows only) Visual C++ (<https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170>)

### 2. Authenticate to Azure using Azure CLI[​](#2-authenticate-to-azure-using-azure-cli "Direct link to 2. Authenticate to Azure using Azure CLI")

```bash
az login

```

The profiler uses Azure SDK's `DefaultAzureCredential` which attempts authentication in this order:

1. **Environment Variables** (Service Principal):

   <!-- -->

   * `AZURE_TENANT_ID`
   * `AZURE_CLIENT_ID`
   * `AZURE_CLIENT_SECRET`

2. **Managed Identity** (if running on Azure VM/App Service)

3. **Azure CLI** (from `az login`)

4. **Visual Studio Code**

5. **Azure PowerShell**

Ensure your user/service principal account has the required roles listed below. See [Azure documentation](https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli?view=azure-cli-latest) for more details.

### 3. Required Access to Synapse Workspace[​](#3-required-access-to-synapse-workspace "Direct link to 3. Required Access to Synapse Workspace")

Attention:

Skip this prerequisite if you are using a standalone SQL Dedicated Pool (formerly Azure SQL DW) and NOT a Synapse Workspace

* Profiler uses the Python version of Azure SDK libraries to extract information about target Synapse Workspace.

* For making the Azure API calls using Azure SDK, the authenticated identity (user or service principal) needs the following role assignments. Just giving the Synapse Administrator role is not enough. The below roles must be explicitly assigned.

  <!-- -->

  * Synapse Artifact User.
    <!-- -->
    * Assign from Synapse Workspace → Manage Access → Access Control [Refer to Azure documentation](https://learn.microsoft.com/en-us/azure/synapse-analytics/security/how-to-manage-synapse-rbac-role-assignments)
  * Monitoring Reader
    <!-- -->
    * Assign from Synapse Workspace → IAM

### 4. Setup user\_id/password for ODBC connectivity[​](#4-setup-user_idpassword-for-odbc-connectivity "Direct link to 4. Setup user_id/password for ODBC connectivity")

Create/Use a user with access to query the following tables in Synapse.

Attention:

The user should not have Multi-factor Authentication (MFA) enabled as ODBC does not support MFA

This user id needs to have read access (`SELECT grants`) on the following tables. The following permissions are required for Dynamic Management Views (DMVs):

* **`VIEW DATABASE STATE`** - Required for database-scoped DMVs (queries within specific databases)
* **`VIEW SERVER STATE`** - Required for server-level DMVs (queries in the `master` database)
* **`VIEW DEFINITION`** - Required to view metadata definitions

```sql
-- Grant database-level permissions
GRANT VIEW DATABASE STATE TO <user_id>
GRANT VIEW DEFINITION TO <user_id>

-- Grant server-level permissions (CRITICAL for serverless pool DMVs)
-- Connect to master database first
USE master
GO
GRANT VIEW SERVER STATE TO <user_id>

```

| Pool Type           | Category          | Objects                                                                                                                                                                                     |
| ------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dedicated SQL Pool  | Tables            | sys.databases<br />information\_schema.tables<br />information\_schema.columns<br />information\_schema.views<br />information\_schema.routines                                             |
|                     | DMVs              | sys.dm\_pdw\_exec\_sessions<br />sys.dm\_pdw\_exec\_requests<br />sys.dm\_pdw\_nodes\_db\_partition\_stats                                                                                  |
| Serverless SQL Pool | Catalog Views     | sys.databases<br />information\_schema.tables<br />information\_schema.columns<br />information\_schema.views<br />sys.objects (for routines)                                               |
|                     | Server DMVs<br /> | sys.dm\_exec\_sessions<br />sys.dm\_exec\_requests<br />sys.dm\_exec\_query\_stats<br />sys.dm\_exec\_sql\_text<br />sys.dm\_exec\_requests\_history<br />sys.dm\_external\_data\_processed |

## Configure Connection to Synapse[​](#configure-connection-to-synapse "Direct link to Configure Connection to Synapse")

```console
databricks labs lakebridge configure-database-profiler

Please select the source system you want to configure
[0] synapse
Enter a number between 0 and 0: 0

(local | env)
local means values are read as plain text
env means values are read from environment variables, and fall back to plain text if not variable is not found

Enter secret vault type (local | env)
[0] env
[1] local
Enter a number between 0 and 1: 1
Please provide Synapse Workspace settings:
Enter Synapse workspace name: synapse
Enter SQL user: user
Enter SQL password:
Enter timezone (e.g. America/New_York) (default: UTC):
Enter the ODBC driver installed locally (default: ODBC Driver 18 for SQL Server):
Please provide Azure access settings:
Enter development endpoint: synapse.endpoint
Please select JDBC authentication type:
Select authentication type
[0] ad_passwd_authentication
[1] spn_authentication
[2] sql_authentication
Enter a number between 0 and 2: 2
Enter fetch size (default: 1000):
Enter login timeout (seconds) (default: 30):
Exclude serverless SQL pool from profiling? (default: no):
Exclude dedicated SQL pools from profiling? (default: no):
Exclude Spark pools from profiling? (default: no):
Exclude monitoring metrics from profiling? (default: no):
Redact SQL pools SQL text? (default: no):

```

[Back to Configure Profiler](/lakebridge/docs/assessment/profiler.md#configure-profiler)
