/**
 * Retrieves system-level information from SQL Server using sys.dm_os_sys_info.
 * Returns details about memory, CPU, scheduler count, and other OS-related
 * metadata for the SQL Server instance, along with a timestamp indicating when
 * the data was extracted.
 */
SELECT *,
       Sysdatetime() AS extract_ts
  FROM   sys.dm_os_sys_info
