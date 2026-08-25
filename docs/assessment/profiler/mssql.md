# MSSQL Profiler Details

* [Prerequisites](#prerequisites)
* [Profiled Tables and Views](#profiled-tables-and-views)
* [Configure Connection to MSSQL](#configure-connection-to-mssql)
* [Execute the Profiler](#execute-the-profiler)

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

### 3. Required Database Permissions[​](#3-required-database-permissions "Direct link to 3. Required Database Permissions")

The SQL user configured for the profiler must have read access to the system catalog views and Dynamic Management Views (DMVs) listed in [Profiled Tables and Views](#profiled-tables-and-views).

Permissions differ depending on whether you are running against an **on-premises SQL Server** or **Azure SQL Database**.

#### On-Premises SQL Server[​](#on-premises-sql-server "Direct link to On-Premises SQL Server")

On a self-hosted SQL Server instance, a server-level grant is sufficient:

```sql
-- Grant access to Dynamic Management Views (DMVs)
GRANT VIEW SERVER STATE TO [<user_name>];

-- Grant access to object definitions (routines, views)
GRANT VIEW DEFINITION TO [<user_name>];

-- Grant read access to INFORMATION_SCHEMA views
GRANT SELECT ON SCHEMA::INFORMATION_SCHEMA TO [<user_name>];

```

tip

`VIEW SERVER STATE` is a server-level permission required to query `sys.dm_*` Dynamic Management Views. `VIEW DEFINITION` allows the user to see the definitions of stored procedures and views. These grants should be executed by a `sysadmin` or a login with `CONTROL SERVER` permission.

#### Azure SQL Database[​](#azure-sql-database "Direct link to Azure SQL Database")

Attention:

`VIEW SERVER STATE` is **not supported** on Azure SQL Database. You must use `VIEW DATABASE STATE` instead, which must be granted **per database** — including the `master` database.

First, identify your target database(s) by connecting as an admin and running:

```sql
SELECT name FROM sys.databases WHERE database_id > 4;

```

Then grant permissions in both `master` and each target database:

```sql
-- In the master database (required for server-level DMVs like sys.databases, sys.dm_os_sys_info)
USE master;
CREATE USER [<user_name>] FROM LOGIN [<user_name>];
GRANT VIEW DATABASE STATE TO [<user_name>];

-- In each target database
USE [<target_database>];
CREATE USER [<user_name>] FROM LOGIN [<user_name>];
GRANT VIEW DATABASE STATE TO [<user_name>];
GRANT VIEW DEFINITION TO [<user_name>];
GRANT SELECT ON SCHEMA::INFORMATION_SCHEMA TO [<user_name>];

```

tip

On Azure SQL Database, `VIEW DATABASE STATE` is scoped to a single database. The profiler queries server-level DMVs (e.g., `sys.databases`, `sys.dm_os_sys_info`) in the `master` database context, so the user must have `VIEW DATABASE STATE` granted there in addition to the target database(s).

## Profiled Tables and Views[​](#profiled-tables-and-views "Direct link to Profiled Tables and Views")

The MSSQL profiler executes queries against the following system tables and DMVs. The results are organized into two extraction steps: **schema metadata** and **activity metrics**.

### Schema Metadata[​](#schema-metadata "Direct link to Schema Metadata")

| Query          | Source Table(s)                            | Description                                                                                            |
| -------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| System Info    | `sys.dm_os_sys_info`                       | Instance-level metadata: CPU topology, memory, virtualization, and SQL Server start time.              |
| Databases      | `sys.databases`                            | Lists all user databases (excluding system databases) with IDs, names, collation, and creation dates.  |
| Tables         | `INFORMATION_SCHEMA.TABLES`                | Extracts table definitions and types from each database.                                               |
| Views          | `INFORMATION_SCHEMA.VIEWS`                 | Extracts view definitions (SQL text is redacted for security).                                         |
| Columns        | `INFORMATION_SCHEMA.COLUMNS`               | Column-level metadata including data types, nullability, precision, collation, and domain information. |
| Indexed Views  | `sys.views`, `sys.indexes`                 | Identifies views that have clustered or non-clustered indexes, with index type and IDs.                |
| Routines       | `INFORMATION_SCHEMA.ROUTINES`              | Stored procedures and user-defined functions (routine definitions are redacted for security).          |
| Database Sizes | `sys.database_files`                       | Database file metadata including current size, free space, and maximum configured size (in MB).        |
| Table Sizes    | `sys.dm_db_partition_stats`, `sys.objects` | Per-table storage metrics: row counts, reserved/used/unused space, and data vs. index space breakdown. |

### Activity Metrics[​](#activity-metrics "Direct link to Activity Metrics")

| Query           | Source Table(s)                                   | Description                                                                                                                                                            |
| --------------- | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Query Stats     | `sys.dm_exec_query_stats`, `sys.dm_exec_sql_text` | Recently executed queries classified by command type (QUERY, DML, DDL, ROUTINE, TRANSACTION\_CONTROL, OTHER) with execution count, duration, CPU time, and row counts. |
| Procedure Stats | `sys.dm_exec_procedure_stats`                     | Stored procedure execution statistics including execution counts, total CPU time, and elapsed time.                                                                    |
| Sessions        | `sys.dm_exec_sessions`                            | Active user sessions with login info (hashed), program names, CPU/memory usage, and request timing.                                                                    |
| CPU Utilization | `sys.dm_os_ring_buffers`, `sys.dm_os_sys_info`    | CPU utilization over time, including system idle percentage and SQL Server process utilization.                                                                        |

## Configure Connection to MSSQL[​](#configure-connection-to-mssql "Direct link to Configure Connection to MSSQL")

Run the following command to configure the profiler connection to your SQL Server instance:

```console
databricks labs lakebridge configure-database-profiler

Please select the source system you want to configure
[0] synapse
[1] mssql
Enter a number between 0 and 1: 1

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
Enter the fully-qualified server name: my-server.database.windows.net
Enter the port details: 1433
Enter the database name: MyAppDB
Trust server certificate (default: no):
Enter timezone (e.g. America/New_York) (default: UTC):
Do you want to test the connection to mssql? (yes/no): yes

```

For `DefaultAzureCredential` and `ActiveDirectoryServicePrincipal`, the `Enter the username` / `Enter the password` prompts are skipped — the identity is resolved at run time (`az login` or `AZURE_*` env vars).

### Configuration Parameters[​](#configuration-parameters "Direct link to Configuration Parameters")

| Parameter                    | Description                                                                                                                                                                                                                                       | Default       |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| **Secret vault type**        | `local` for plain text values, `env` to read from environment variables                                                                                                                                                                           | —             |
| **Authentication method**    | One of the four modes in the table above                                                                                                                                                                                                          | `SqlPassword` |
| **Username / Password**      | Required for `SqlPassword` and `ActiveDirectoryPassword`. Skipped for `DefaultAzureCredential` and `ActiveDirectoryServicePrincipal`.                                                                                                             | —             |
| **Fetch size**               | Number of rows fetched per batch from the source                                                                                                                                                                                                  | `1000`        |
| **Login timeout**            | Connection timeout in seconds                                                                                                                                                                                                                     | `30`          |
| **Server name**              | Fully-qualified SQL Server hostname                                                                                                                                                                                                               | —             |
| **Port**                     | SQL Server port number                                                                                                                                                                                                                            | —             |
| **Database**                 | Database to connect to. `INFORMATION_SCHEMA` queries are scoped to this database; other databases on the instance are still discovered via `sys.databases`.                                                                                       | —             |
| **Trust server certificate** | Skip TLS certificate validation when connecting. Set to `yes` only when the server uses a self-signed or untrusted certificate (e.g., local/dev SQL Server). Leave as `no` for Azure SQL Database and production servers with valid certificates. | `no`          |
| **Timezone**                 | Timezone for timestamp normalization                                                                                                                                                                                                              | `UTC`         |

## Execute the Profiler[​](#execute-the-profiler "Direct link to Execute the Profiler")

Once configured, run the profiler to extract metadata and activity metrics from your SQL Server instance:

```bash
databricks labs lakebridge execute-database-profiler --source-tech mssql

```

The profiler will:

1. Connect to your SQL Server instance using the configured credentials
2. Execute the schema metadata and activity metric extraction queries
3. Store the results in a local DuckDB extract file

[Back to Configure Profiler](/lakebridge/docs/assessment/profiler.md#configure-profiler)
