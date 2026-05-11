/**
 * Extracts SQL Server CPU and system utilization metrics from `sys.dm_os_ring_buffers`,
 * including system idle and SQL process utilization over time.
 */
WITH process_utilization_info
     AS (SELECT record.value('(./Record/@id)[1]', 'int')
                   AS record_id,
                [timestamp],
record.value('(./Record/SchedulerMonitorEvent/SystemHealth/SystemIdle)[1]',
'int')
           AS SystemIdle,
record.value('(./Record/SchedulerMonitorEvent/SystemHealth/ProcessUtilization)[1]', 'int') AS SQLProcessUtilization
 FROM   (SELECT [timestamp],
                CONVERT(XML, record) AS record
         FROM   sys.dm_os_ring_buffers
         WHERE  ring_buffer_type = N'RING_BUFFER_SCHEDULER_MONITOR'
                AND record LIKE '%<SystemHealth>%') X),
     os_sysinfo
     AS (SELECT TOP 1 ms_ticks
         FROM   sys.dm_os_sys_info),
     cpu_utilization
     AS (SELECT record_id,
                Dateadd (ms, ( [timestamp] - ms_ticks ), Getdate()) AS EventTime
                ,
                systemidle,
                sqlprocessutilization
         FROM   process_utilization_info
                CROSS JOIN os_sysinfo)
SELECT *,
       Sysdatetime() AS extract_ts
FROM   cpu_utilization
ORDER  BY eventtime
