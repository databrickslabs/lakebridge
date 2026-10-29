/**
 * Retrieves system-level information from SQL Server using sys.dm_os_sys_info.
 * Returns details about memory, CPU, scheduler count, and other OS-related
 * metadata for the SQL Server instance, along with a timestamp indicating when
 * the data was extracted.
 *
 * FIXME: trim both this query and sys_info_ddl.sql to the columns that matter
 * Many columns are internal scheduler and OS bookkeeping. On Azure SQL Database
 * (EngineEdition5) most hardware/memory columns return NULL anyway.
 */
/*
 * sys.dm_os_sys_info gained columns over SQL Server versions (e.g. process_physical_affinity in
 * 2017). Each such column gets a NULL default in `defaults`; the inner query references it
 * unqualified, so on versions that have the column it binds to the DMV, and on older versions it
 * falls back to the outer default instead of failing to compile.
 */
SELECT s.cpu_ticks,
       s.ms_ticks,
       s.cpu_count,
       s.hyperthread_ratio,
       s.physical_memory_kb,
       s.virtual_memory_kb,
       s.committed_kb,
       s.committed_target_kb,
       s.visible_target_kb,
       s.stack_size_in_bytes,
       s.os_quantum,
       s.os_error_mode,
       s.os_priority_class,
       s.max_workers_count,
       s.scheduler_count,
       s.scheduler_total_count,
       s.deadlock_monitor_serial_number,
       s.sqlserver_start_time_ms_ticks,
       s.sqlserver_start_time,
       s.affinity_type,
       s.affinity_type_desc,
       s.process_kernel_time_ms,
       s.process_user_time_ms,
       s.time_source,
       s.time_source_desc,
       s.virtual_machine_type,
       s.virtual_machine_type_desc,
       s.softnuma_configuration,
       s.softnuma_configuration_desc,
       s.process_physical_affinity,
       s.sql_memory_model,
       s.sql_memory_model_desc,
       s.socket_count,
       s.cores_per_socket,
       s.numa_node_count,
       s.container_type,
       s.container_type_desc,
       Sysdatetime() AS extract_ts
FROM   (SELECT CAST(NULL AS int) AS softnuma_configuration,
             CAST(NULL AS nvarchar(60)) AS softnuma_configuration_desc,
             CAST(NULL AS nvarchar(3072)) AS process_physical_affinity,
             CAST(NULL AS int) AS sql_memory_model,
             CAST(NULL AS nvarchar(60)) AS sql_memory_model_desc,
             CAST(NULL AS int) AS socket_count,
             CAST(NULL AS int) AS cores_per_socket,
             CAST(NULL AS int) AS numa_node_count,
             CAST(NULL AS int) AS container_type,
             CAST(NULL AS nvarchar(60)) AS container_type_desc) AS defaults
       CROSS APPLY (SELECT cpu_ticks,
                    ms_ticks,
                    cpu_count,
                    hyperthread_ratio,
                    physical_memory_kb,
                    virtual_memory_kb,
                    committed_kb,
                    committed_target_kb,
                    visible_target_kb,
                    stack_size_in_bytes,
                    os_quantum,
                    os_error_mode,
                    os_priority_class,
                    max_workers_count,
                    scheduler_count,
                    scheduler_total_count,
                    deadlock_monitor_serial_number,
                    sqlserver_start_time_ms_ticks,
                    sqlserver_start_time,
                    affinity_type,
                    affinity_type_desc,
                    process_kernel_time_ms,
                    process_user_time_ms,
                    time_source,
                    time_source_desc,
                    virtual_machine_type,
                    virtual_machine_type_desc,
                    softnuma_configuration,
                    softnuma_configuration_desc,
                    process_physical_affinity,
                    sql_memory_model,
                    sql_memory_model_desc,
                    socket_count,
                    cores_per_socket,
                    numa_node_count,
                    container_type,
                    container_type_desc
                    FROM   sys.dm_os_sys_info) AS s
