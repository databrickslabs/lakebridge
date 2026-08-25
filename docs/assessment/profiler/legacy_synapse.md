# Legacy Synapse Profiler Details

* [Prerequisites](#prerequisites)
* [Profiled Tables and Views](#profiled-tables-and-views)
* [Configure Connection to Legacy Synapse](#configure-connection-to-legacy-synapse)
* [Execute the Profiler](#execute-the-profiler)

info

This profiler targets **Azure Synapse dedicated SQL pool** (formerly Azure SQL Data Warehouse). For serverless SQL pools, Spark pools, and the Synapse workspace control plane, use the [Synapse profiler](/lakebridge/docs/assessment/profiler/synapse.md) instead.

## Prerequisites[​](#prerequisites "Direct link to Prerequisites")

### 1. Download[​](#1-download "Direct link to 1. Download")

No driver installation is required: the profiler connects with Microsoft's [mssql-python](https://github.com/microsoft/mssql-python) driver, which bundles its own connectivity layer.

### 2. Authentication[​](#2-authentication "Direct link to 2. Authentication")

The profiler supports the following authentication modes (`configure-database-profiler` prompts for one):

| Auth method                       | Description                                                                             | MFA-capable |
| --------------------------------- | --------------------------------------------------------------------------------------- | ----------- |
| `SqlPassword`                     | SQL Authentication — username + password from credentials file                          | No          |
| `DefaultAzureCredential`          | Entra ID via the Azure Identity credentials chain. Recommended for Azure-hosted targets | Yes         |
| `ActiveDirectoryPassword`         | Entra ID (Azure AD) username + password                                                 | No          |
| `ActiveDirectoryServicePrincipal` | Service Principal — `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` env vars                  | No          |

warning

For `ActiveDirectoryServicePrincipal`, set `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET` env vars before running the profiler. For `DefaultAzureCredential`, run `az login` first; for unattended runs set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET`.

### 3. Azure Monitor (CPU / DWU utilization metrics)[​](#3-azure-monitor-cpu--dwu-utilization-metrics "Direct link to 3. Azure Monitor (CPU / DWU utilization metrics)")

The profiler collects dedicated-pool utilization metrics (CPU %, DWU %, memory %, active/queued queries, and more) from **Azure Monitor**. This requires the identity resolved by your chosen authentication method to hold the **Monitoring Reader** role on the dedicated pool.

### 4. Required Database Permissions[​](#4-required-database-permissions "Direct link to 4. Required Database Permissions")

The SQL user configured for the profiler must have read access to the system catalog views and PDW Dynamic Management Views listed in [Profiled Tables and Views](#profiled-tables-and-views).

Connect to the target dedicated SQL pool as an admin and run:

```sql
-- Create a contained user from the server login
CREATE USER [<user_name>] FROM LOGIN [<user_name>];

-- Grant access to PDW Dynamic Management Views (sys.dm_pdw_*)
GRANT VIEW DATABASE STATE TO [<user_name>];

-- Grant access to object definitions (routines, views)
GRANT VIEW DEFINITION TO [<user_name>];

-- Grant read access to INFORMATION_SCHEMA views
GRANT SELECT ON SCHEMA::INFORMATION_SCHEMA TO [<user_name>];

```

tip

The profiler queries server-level catalog views such as `sys.databases` from the target database context. `VIEW DATABASE STATE` covers the `sys.dm_pdw_*` DMVs used to extract sessions, requests, and per-node storage statistics.

## Profiled Tables and Views[​](#profiled-tables-and-views "Direct link to Profiled Tables and Views")

The profiler executes queries against the following system tables and PDW DMVs. The results are organized into two extraction steps: **schema metadata** and **activity metrics**.

### Schema Metadata[​](#schema-metadata "Direct link to Schema Metadata")

| Query     | Source Table(s)               | Description                                                                                            |
| --------- | ----------------------------- | ------------------------------------------------------------------------------------------------------ |
| Databases | `sys.databases`               | Lists each database by name.                                                                           |
| Tables    | `INFORMATION_SCHEMA.TABLES`   | Extracts table definitions and types from the dedicated SQL pool.                                      |
| Views     | `INFORMATION_SCHEMA.VIEWS`    | Extracts view definitions (SQL text is redacted for security).                                         |
| Columns   | `INFORMATION_SCHEMA.COLUMNS`  | Column-level metadata including data types, nullability, precision, collation, and domain information. |
| Routines  | `INFORMATION_SCHEMA.ROUTINES` | Stored procedures and user-defined functions (routine definitions are redacted for security).          |

### Activity Metrics[​](#activity-metrics "Direct link to Activity Metrics")

| Query        | Source Table(s)                       | Description                                                                                                                          |
| ------------ | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Requests     | `sys.dm_pdw_exec_requests`            | Recently executed PDW requests with submit/start/end times, total elapsed time, status, resource class, and the originating command. |
| Sessions     | `sys.dm_pdw_exec_sessions`            | Active user sessions with login info, app names, query counts, and transaction state. System sessions are excluded.                  |
| Storage Info | `sys.dm_pdw_nodes_db_partition_stats` | Per-compute-node storage usage: reserved and used space (MB), aggregated across all partitions on each node.                         |

### Utilization Metrics (Azure Monitor)[​](#utilization-metrics-azure-monitor "Direct link to Utilization Metrics (Azure Monitor)")

| Query              | Source                                            | Description                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------ | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Monitoring Metrics | Azure Monitor (`Microsoft.Sql/servers/databases`) | Dedicated-pool utilization over the profiling window: `cpu_percent`, `dwu_consumption_percent`, `dwu_limit`, `dwu_used`, `memory_usage_percent`, `physical_data_read_percent`, `local_tempdb_usage_percent`, `active_queries`, and `queued_queries`. Requires the Azure settings and Monitoring Reader role described in [Azure Monitor](#3-azure-monitor-cpu--dwu-utilization-metrics). |

## Configure Connection to Legacy Synapse[​](#configure-connection-to-legacy-synapse "Direct link to Configure Connection to Legacy Synapse")

Run the following command to configure the profiler connection to your dedicated SQL pool:

```console
databricks labs lakebridge configure-database-profiler

Please select the source system you want to configure
[0] synapse
[1] mssql
[2] legacy_synapse
Enter a number between 0 and 2: 2

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
Enter the username: profiler_user
Enter the password:
Enter fetch size (default: 1000):
Enter login timeout (seconds) (default: 30):
Enter the fully-qualified server name: my-dw-server.database.windows.net
Enter the port details: 1433
Enter the database name: my_pool
Trust server certificate (default: no):
Enter timezone (e.g. America/New_York) (default: UTC):
Enter the Azure subscription ID: 00000000-0000-0000-0000-000000000000
Enter the Azure resource group: rg-analytics

```

### Configuration Parameters[​](#configuration-parameters "Direct link to Configuration Parameters")

| Parameter                    | Description                                                                                                                                      | Default       |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------- |
| **Secret vault type**        | `local` for plain text values, `env` to read from environment variables                                                                          | —             |
| **Authentication method**    | One of the four modes in the table above                                                                                                         | `SqlPassword` |
| **Username / Password**      | Required for `SqlPassword` and `ActiveDirectoryPassword`. Skipped for `DefaultAzureCredential` and `ActiveDirectoryServicePrincipal`.            | —             |
| **Fetch size**               | Number of rows fetched per batch from the source                                                                                                 | `1000`        |
| **Login timeout**            | Connection timeout in seconds                                                                                                                    | `30`          |
| **Server name**              | Fully-qualified hostname of the dedicated SQL pool                                                                                               | —             |
| **Port**                     | Server port number                                                                                                                               | —             |
| **Database**                 | The dedicated SQL pool name to connect to. Dedicated pools do not have a `master` database, so this must be set to the pool you want to profile. | —             |
| **Trust server certificate** | Skip TLS certificate validation when connecting. Leave as `no` for Azure-hosted dedicated SQL pools, which present valid certificates.           | `no`          |
| **Timezone**                 | Timezone for timestamp normalization                                                                                                             | `UTC`         |
| **Azure subscription ID**    | Subscription containing the dedicated pool. Used to build the Azure resource ID for the Azure Monitor metrics extract.                           | —             |
| **Azure resource group**     | Resource group containing the dedicated pool's logical SQL server.                                                                               | —             |

## Execute the Profiler[​](#execute-the-profiler "Direct link to Execute the Profiler")

Once configured, run the profiler to extract metadata and activity metrics from your dedicated SQL pool:

```bash
databricks labs lakebridge execute-database-profiler --source-tech legacy_synapse

```

The profiler will:

1. Connect to your dedicated SQL pool using the configured credentials
2. Execute the schema metadata and activity metric extraction queries
3. Store the results in a local DuckDB extract file

[Back to Configure Profiler](/lakebridge/docs/assessment/profiler.md#configure-profiler)
