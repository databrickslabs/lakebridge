# Synapse Profiler Details

* [Prerequisites](#prerequisites)
* [Configure Connection to Synapse](#configure-connection-to-synapse)

## Prerequisites[​](#prerequisites "Direct link to Prerequisites")

### 1. Download[​](#1-download "Direct link to 1. Download")

* **Azure CLI** — required for authenticating to the Azure Management REST API path (workspace metadata, pool listing, monitoring metrics). [Download](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli).

No database driver installation is required: the profiler connects to SQL pools with Microsoft's [mssql-python](https://github.com/microsoft/mssql-python) driver, which bundles its own connectivity layer.

### 2. Authentication[​](#2-authentication "Direct link to 2. Authentication")

info

This page covers a full Synapse Workspace. If you are profiling a standalone SQL Dedicated Pool (formerly Azure SQL DW), use the [Legacy Synapse profiler](/lakebridge/docs/assessment/profiler/legacy_synapse.md) instead.

The synapse profiler has **two auth paths**:

#### Azure Management REST API (workspace metadata, pool listing, monitoring metrics)[​](#azure-management-rest-api-workspace-metadata-pool-listing-monitoring-metrics "Direct link to Azure Management REST API (workspace metadata, pool listing, monitoring metrics)")

Uses Azure SDK's `DefaultAzureCredential`. Run `az login` first or set env vars `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET`.

The authenticated identity needs the **Synapse Artifact User** and **Monitoring Reader** roles on the workspace. Granting Synapse Administrator alone is **not** enough — both roles must be explicitly assigned. Assign from Synapse Workspace → Manage Access → Access Control and Synapse Workspace → IAM respectively. See [Azure documentation](https://learn.microsoft.com/en-us/azure/synapse-analytics/security/how-to-manage-synapse-rbac-role-assignments).

#### SQL connection to SQL pools[​](#sql-connection-to-sql-pools "Direct link to SQL connection to SQL pools")

Picking authentication mode `DefaultAzureCredential` will use the same credentials as the previous section. The authenticated identity needs the right permissions on the sql pools listed in [section 3](#3-required-database-permissions). Or pick one of the other authentication modes (`configure-database-profiler` prompts for one):

| Auth method                       | Description                                                                             | MFA-capable |
| --------------------------------- | --------------------------------------------------------------------------------------- | ----------- |
| `SqlPassword`                     | SQL Authentication — username + password from credentials file                          | No          |
| `DefaultAzureCredential`          | Entra ID via the Azure Identity credentials chain. Recommended for Azure-hosted targets | Yes         |
| `ActiveDirectoryPassword`         | Entra ID (Azure AD) username + password                                                 | No          |
| `ActiveDirectoryServicePrincipal` | Service Principal — `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` env vars                  | No          |

warning

For `ActiveDirectoryServicePrincipal`, set `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET` env vars before running the profiler. For `DefaultAzureCredential`, run `az login` first; for unattended runs set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET`.

### 3. Required Database Permissions[​](#3-required-database-permissions "Direct link to 3. Required Database Permissions")

The SQL user configured for the profiler must have read access (`SELECT grants`) to the following tables. The following permissions are required for Dynamic Management Views (DMVs):

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
Select authentication method
[0] SqlPassword
[1] DefaultAzureCredential
[2] ActiveDirectoryPassword
[3] ActiveDirectoryServicePrincipal
Enter a number between 0 and 3: 0
Enter the username: user
Enter the password:
Please provide Synapse Workspace settings:
Enter Synapse workspace name: synapse
Enter development endpoint: synapse.endpoint
Enter fetch size (default: 1000):
Enter login timeout (seconds) (default: 30):
Enter timezone (e.g. America/New_York) (default: UTC):
Exclude serverless SQL pool from profiling? (default: no):
Exclude dedicated SQL pools from profiling? (default: no):
Exclude Spark pools from profiling? (default: no):
Exclude monitoring metrics from profiling? (default: no):
Redact SQL pools SQL text? (default: no):

```

[Back to Configure Profiler](/lakebridge/docs/assessment/profiler.md#configure-profiler)
